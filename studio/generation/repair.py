"""User-selected repair of an audited generation, without replanning content."""

import asyncio
import json
import shutil
import time

from studio.composition.artifacts import public_path
from studio.checks.audit import audit_scenes
from ..config import Settings
from studio.composition.contracts import candidates
from studio.composition.layout_edits import LayoutEdit
from ..models import Plans, SlideScene
from studio.checks.refinement import apply_edits
from studio.composition.render import render_variant
from studio.checks.review_snapshot import load_snapshot, snapshot_hash
from ..stage_runtime import GenerationDeadline
from .audit import audit_diversity, audit_variant_exports, summarize_audits
from .publication import publish_generation
from .results import (
    CompositionResult,
    ContentReviewResult,
    DiversityResult,
    EngineReport,
    PlanningResult,
    RefinementReport,
    ReviewedGeneration,
    RenderingResult,
    VariantResult,
    VisualReviewResult,
)
from .reviews import review_exported_content
from studio.checks.visual import review_visuals


def _copy_deliverables(store, source_id: str, target_id: str, plans: Plans) -> None:
    source, target = store.directory(source_id), store.directory(target_id)
    plan_file = public_path(source, "plans.json")
    if plan_file is None:
        raise ValueError("Исходный план недоступен")
    shutil.copyfile(plan_file, target / "plans.json")
    for variant in plans.variants:
        folder = target / variant.key
        folder.mkdir()
        for name in ("deck.pptx", "deck.pdf", "deck.html", "slides.json", "FONT_LICENSES.txt"):
            item = public_path(source, f"{variant.key}/{name}")
            if item is not None:
                shutil.copyfile(item, folder / name)
        for number in range(1, len(variant.slides) + 1):
            item = public_path(source, f"{variant.key}/slide-{number}.png")
            if item is None:
                raise ValueError("Исходный предпросмотр неполон")
            shutil.copyfile(item, folder / item.name)


def _selected_edits(package, plans, decks, selected):
    by_variant = {v.key: v for v in plans.variants}
    requested = {}
    for finding in selected:
        if not finding.action or not finding.variant or not finding.slide:
            raise ValueError("Выбрано замечание без безопасной операции")
        key = (finding.variant, finding.slide)
        if key in requested and requested[key] != finding.action:
            raise ValueError("Для одного слайда выбраны несовместимые операции")
        requested[key] = finding.action
    allowed = {}
    for (variant, slide), action in requested.items():
        plan = by_variant[variant].slides[slide - 1]
        if action == "readable_chart":
            if plan.layout != "chart" or plan.chart_style == "readable":
                raise ValueError("Диаграмма уже использует читаемый режим")
            allowed[(variant, slide)] = ["@readable_chart"]
        else:
            current = decks[variant][slide - 1].pattern_id
            allowed[(variant, slide)] = [
                p.id for p in candidates(package, plan, slide - 1) if p.id != current
            ]
            if not allowed[(variant, slide)]:
                raise ValueError("Нет совместимого макета для выбранного слайда")
    edits = []
    for (variant, slide), choices in allowed.items():
        if choices == ["@readable_chart"]:
            candidates_for_slide = [
                LayoutEdit(variant=variant, slide=slide, operation="readable_chart")
            ]
        else:
            candidates_for_slide = [
                LayoutEdit(variant=variant, slide=slide, pattern_id=choice) for choice in choices
            ]
        for edit in candidates_for_slide:
            try:
                apply_edits(package, plans, decks, [edit], {(variant, slide): choices})
            except ValueError:
                continue
            edits.append(edit)
            break
        else:
            raise ValueError("Ни один безопасный вариант исправления не прошёл локальную проверку")
    trial, changed = apply_edits(package, plans, decks, edits, allowed)
    return trial, changed, edits


async def run_repair(
    store, jid: str, settings: Settings, gateway, package, source_versions, source_revision
) -> None:
    job = store.get(jid)
    if job.get("operation") != "repair" or not job.get("parent_generation_id"):
        raise ValueError("Задание не является выборочным исправлением")
    parent_id = job["parent_generation_id"]
    snapshot = load_snapshot(store, parent_id)
    if snapshot_hash(snapshot) != job.get("audit_hash"):
        raise ValueError("Аудит изменился после выбора исправлений")
    ids = job.get("finding_ids", [])
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Выберите замечания для исправления")
    by_id = {f.id: f for f in snapshot.findings}
    if set(ids) - by_id.keys():
        raise ValueError("Неизвестное замечание аудита")
    selected = [by_id[i] for i in ids]
    if package.id != snapshot.package_id:
        raise ValueError("Подготовленный пакет не соответствует исходному аудиту")
    source, directory = store.directory(parent_id), store.directory(jid)
    plan_path = public_path(source, "plans.json")
    if plan_path is None:
        raise ValueError("Исходный план недоступен")
    plans = Plans.model_validate_json(plan_path.read_text())
    decks = {}
    for variant in plans.variants:
        scene_path = public_path(source, f"{variant.key}/slides.json")
        if scene_path is None:
            raise ValueError("Сцены исходной версии недоступны")
        decks[variant.key] = [
            SlideScene.model_validate(s) for s in json.loads(scene_path.read_text())
        ]
    trial, changed, edits = _selected_edits(package, plans, decks, selected)
    _copy_deliverables(store, parent_id, jid, plans)
    store.update(jid, "running", phase="Применяем выбранные исправления", progress=35)
    template = store.directory(package.id) / "input.pptx"
    renderings = {}
    for key, scenes in changed.items():
        renderings[key] = await asyncio.to_thread(
            render_variant, scenes, package.template, template, directory / key
        )
        decks[key] = scenes
    (directory / "plans.json").write_text(trial.model_dump_json(indent=2))
    parent_manifest_path = public_path(source, "audit-input.json")
    if parent_manifest_path is None:
        raise ValueError("Исходный манифест недоступен")
    parent_manifest = json.loads(parent_manifest_path.read_text())
    source_variants = {v["key"]: v for v in parent_manifest["variants"]}
    variants = []
    for variant in trial.variants:
        old = source_variants[variant.key]
        variants.append(
            VariantResult(
                key=variant.key,
                title=variant.title,
                slides=len(variant.slides),
                rendering=RenderingResult.model_validate(
                    renderings.get(variant.key, old["rendering"])
                ),
                template_strategies=sorted({s.strategy for s in decks[variant.key]}),
                findings=audit_scenes(decks[variant.key], package),
                repairs=[],
                initial_errors=old.get("initial_errors", 0),
            )
        )
    store.update(jid, phase="Повторно проверяем все три презентации", progress=75)
    contextual_raw = {"status": "not_run", "reason": "Модель не подключена", "findings": []}
    if settings.mode == "api":
        contextual_raw = await review_exported_content(trial, package, directory, gateway, 180)
    visual_raw = await review_visuals(
        [r.review_input() for r in variants],
        directory,
        gateway,
        None,
        lambda phase: store.update(jid, phase=phase, progress=82),
        package,
    )
    contextual = ContentReviewResult.model_validate(contextual_raw)
    visual = VisualReviewResult.model_validate(visual_raw)
    (directory / "visual-audit.json").write_text(
        json.dumps(visual.wire(), ensure_ascii=False, indent=2)
    )
    (directory / "visual-audit-initial.json").write_text(
        json.dumps(visual.wire(), ensure_ascii=False, indent=2)
    )
    refinement = RefinementReport(status="completed", attempts=1, accepted=True, edits=edits)
    (directory / "refinement.json").write_text(
        json.dumps(refinement.wire(), ensure_ascii=False, indent=2)
    )
    audit_variant_exports(variants, trial, decks, package, directory)
    prior_diversity = DiversityResult.model_validate(parent_manifest["composition_diversity"])
    diversity = audit_diversity(trial, decks, package, directory, prior_diversity)
    audit = summarize_audits(
        variants,
        package,
        settings,
        diversity,
        contextual,
        visual,
        None,
        parent_manifest.get("planning_source", "model"),
    )
    reviewed = ReviewedGeneration(trial, decks, variants, contextual, visual, refinement)
    composition = CompositionResult(trial, decks, {}, {}, diversity, 0, 0)
    engine = EngineReport.model_validate(parent_manifest["engine"])
    planning = PlanningResult(trial, parent_manifest.get("planning_source", "model"), None, engine)
    deadline = GenerationDeadline(job.get("deadline_at"))
    publish_generation(
        store,
        jid,
        job,
        package,
        settings,
        gateway,
        directory,
        deadline,
        planning,
        composition,
        reviewed,
        audit,
        {"selected_repair_seconds": round(time.time() - job["created"], 3)},
        source_versions,
        source_revision,
    )
