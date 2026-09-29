"""Final read-only audit of the exact files selected for publication."""

from pathlib import Path
from ..models import PreparedPackage, Plans, SlideScene, Finding
from ..config import Settings
from .results import (
    VariantResult,
    SemanticBinding,
    ContentReviewResult,
    VisualReviewResult,
    FinalAuditResult,
    DiversityResult,
    MeaningfulDiversity,
    BackgroundDiversity,
)


def audit_variant_exports(
    results: list[VariantResult],
    plans: Plans,
    decks: dict[str, list[SlideScene]],
    package: PreparedPackage,
    directory: Path,
) -> None:
    from studio.checks.export_audit import audit_export
    from studio.contents.semantic_bindings import binding_report

    for result, variant in zip(results, plans.variants):
        export_findings = audit_export(directory / variant.key / "deck.pptx", variant, package)
        result.findings.extend(Finding.model_validate(f) for f in export_findings)
        result.repairs.extend(result.rendering.object_repairs)
        bindings = [
            binding_report(plan, package, pattern)
            if pattern
            else {
                "status": "general",
                "reason": "Token composition has no source field contract",
                "archetype": plan.purpose,
                "pattern_id": None,
                "fields": [],
            }
            for plan, scene in zip(variant.slides, decks[variant.key])
            for pattern in [
                next((p for p in package.template.patterns if p.id == scene.pattern_id), None)
            ]
        ]
        for binding, scene in zip(bindings, decks[variant.key]):
            if binding["archetype"] in ("comparison", "process", "timeline") and any(
                e.role == "user_image" for e in scene.elements
            ):
                binding.update(status="general", reason="Media uses a shared authored content area")
        result.semantic_bindings = [SemanticBinding.model_validate(b) for b in bindings]


def audit_diversity(
    plans: Plans,
    decks: dict[str, list[SlideScene]],
    package: PreparedPackage,
    directory: Path,
    diversity: DiversityResult,
) -> DiversityResult:
    # Repair may change geometry. Re-check diversity without mutating reviewed slides.
    from studio.checks.export_audit import content_geometry_signature
    from studio.composition.powerpoint import open_presentation

    signatures = {
        v.key: content_geometry_signature(
            open_presentation(directory / v.key / "deck.pptx"), [s.title for s in v.slides]
        )
        for v in plans.variants
    }
    diversity.signatures = signatures
    diversity.distinct = len(set(signatures.values()))
    diversity.verified = diversity.distinct == len(decks)
    diversity.method = "exported_content_geometry"
    from studio.checks.quality import meaningful_diversity
    from studio.checks.export_audit import content_scenes

    exported_decks = {
        v.key: content_scenes(open_presentation(directory / v.key / "deck.pptx"), v, package)
        for v in plans.variants
    }
    meaningful = MeaningfulDiversity.model_validate(
        meaningful_diversity(exported_decks, package.template)
    )
    diversity.meaningful = meaningful
    diversity.verified = diversity.verified and meaningful.verified
    from studio.checks.background_diversity import background_report

    diversity.within_decks = {
        key: BackgroundDiversity.model_validate(background_report(scenes, package.template))
        for key, scenes in decks.items()
    }
    return diversity


def summarize_audits(
    results: list[VariantResult],
    package: PreparedPackage,
    settings: Settings,
    diversity: DiversityResult,
    contextual: ContentReviewResult,
    visual: VisualReviewResult,
    fallback: str | None,
    planning_source: str,
) -> FinalAuditResult:
    warnings = list(package.content.warnings) + [f.message for f in diversity.findings]
    from studio.templates.font_disclosure import unique, warnings as font_warnings

    font_substitutions = unique(
        [item for result in results for item in result.rendering.font_substitutions]
    )
    warnings.extend(font_warnings(font_substitutions))
    from studio.contents.slide_budget import count_was_adjusted

    if count_was_adjusted(package) and package.control.slide_budget.message:
        warnings.append(package.control.slide_budget.message)
    if fallback:
        warnings.append(fallback)
    native_preview = all(r.rendering.native_render for r in results)
    template_degraded = any(
        "native_template" not in r.template_strategies
        or "token_composition" in r.template_strategies
        for r in results
    )
    if not native_preview:
        warnings.append(
            "LibreOffice не найден: предпросмотр построен из модели сцены, а не из готового PPTX."
        )
    if template_degraded:
        warnings.append(
            "Часть слайдов не использует исходные макеты. Соответствие шаблону требует проверки."
        )
    if settings.mode == "extractive":
        warnings.append(
            "Автономный экстрактивный режим: LLM/VLM не использовались. Результат не доказывает качество модельного режима."
        )
    model_degraded = settings.mode == "api" and bool(
        fallback
        or planning_source
        not in ("model", "explicit_author_storyboard", "semantic_summary_storyboard")
        or contextual.status != "completed"
    )
    if model_degraded:
        warnings.append(
            "Модельный путь выполнен не полностью: результат требует проверки, даже если геометрия корректна."
        )
    if visual.status != "completed":
        warnings.append(
            "Визуальная проверка изображений слайдов не выполнена полностью. " + visual.reason
        )
    binding_degraded = any(
        b.archetype in ("comparison", "process", "timeline") and b.status != "specialized"
        for r in results
        for b in r.semantic_bindings
    )
    if binding_degraded:
        warnings.append(
            "Для части специальных архетипов нет однозначной привязки к полям макета. Полный текст сохранён в общем поле; проверьте представление материала."
        )
    errors = (
        sum(f.severity == "error" for r in results for f in r.findings)
        + sum(f.severity == "error" for f in contextual.findings)
        + sum(f.severity == "error" for f in visual.findings)
    )
    return FinalAuditResult(
        results, diversity, warnings, font_substitutions, errors, native_preview, model_degraded
    )
