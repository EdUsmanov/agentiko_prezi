"""Review actual exports and apply bounded revisions before the final audit."""

import json
from pathlib import Path
from collections.abc import Callable
from ..models import PreparedPackage
from studio.providers.gateway import ModelGateway
from ..config import Settings
from studio.jobs.store import Store
from ..stage_runtime import GenerationDeadline
from .results import (
    CompositionResult,
    VariantResult,
    ContentReviewResult,
    VisualReviewResult,
    RefinementReport,
    ReviewedGeneration,
)


async def review_exported_content(plans, package, directory, gateway, timeout):
    from studio.checks.export_audit import slide_text
    from studio.composition.powerpoint import open_presentation

    try:
        facts = {f.id: f for f in package.content.facts}
        slides = []
        for variant in plans.variants:
            exported = open_presentation(directory / variant.key / "deck.pptx")
            slides.extend(
                {
                    "variant": variant.key,
                    "slide": i,
                    "title": plan.title,
                    "actual_text": slide_text(output),
                    "facts": [facts[f].model_dump() for f in plan.fact_ids],
                }
                for i, (plan, output) in enumerate(zip(variant.slides, exported.slides), 1)
            )
        from studio.checks.content_review import review

        return await review(
            slides,
            plans,
            gateway,
            "critic",
            timeout,
            {
                "constraints": package.constraints.model_dump(),
                "slide_budget_adjustment": package.control.slide_budget.model_dump(
                    exclude_defaults=True
                )
                if package.control.slide_budget
                else None,
            },
        )
    except Exception as exc:
        return {"status": "failed", "reason": type(exc).__name__, "findings": []}


async def review_generation(
    composition: CompositionResult,
    results: list[VariantResult],
    package: PreparedPackage,
    directory: Path,
    source: Path,
    gateway: ModelGateway,
    settings: Settings,
    store: Store,
    job_id: str,
    deadline: GenerationDeadline,
    checkpoint: Callable[[str], None],
) -> ReviewedGeneration:
    plans, decks = composition.plans, composition.decks
    contextual = {
        "status": "not_run",
        "reason": "Модель не подключена; контекстуальная оценка не имитируется",
        "findings": [],
    }
    if settings.mode == "api" and deadline.remaining() > 35:
        store.update(job_id, phase="Проверяем смысл и факты в тексте с помощью модели", progress=90)
        contextual = await review_exported_content(
            plans,
            package,
            directory,
            gateway,
            180 if deadline.deadline_at is None else min(25, deadline.remaining(10)),
        )
    from studio.checks.visual import review_visuals

    contextual = ContentReviewResult.model_validate(contextual)
    visual_raw = await review_visuals(
        [r.review_input() for r in results],
        directory,
        gateway,
        None if deadline.deadline_at is None else min(100, deadline.remaining(65)),
        lambda phase: store.update(job_id, phase=phase, progress=91),
        package,
    )
    visual = VisualReviewResult.model_validate(visual_raw)
    (directory / "visual-audit-initial.json").write_text(
        json.dumps(visual.wire(), ensure_ascii=False, indent=2)
    )
    checkpoint("initial_model_reviews_seconds")
    from studio.checks.refinement import refine

    plans, decks, raw_results, visual_raw, refinement_raw = await refine(
        package,
        plans,
        decks,
        [r.review_input() for r in results],
        visual.wire(),
        directory,
        source,
        gateway,
        None if deadline.deadline_at is None else min(110, max(0, deadline.remaining() - 30)),
        lambda phase: store.update(job_id, phase=phase, progress=94),
    )
    results = [VariantResult.model_validate(r) for r in raw_results]
    refinement = RefinementReport.model_validate(refinement_raw)
    visual = VisualReviewResult.model_validate(visual_raw)
    if refinement.accepted:
        contextual = await review_exported_content(
            plans,
            package,
            directory,
            gateway,
            180 if deadline.deadline_at is None else min(20, deadline.remaining(10)),
        )
    checkpoint("refinement_seconds")
    (directory / "refinement.json").write_text(
        json.dumps(refinement.wire(), ensure_ascii=False, indent=2)
    )
    (directory / "visual-audit.json").write_text(
        json.dumps(visual.wire(), ensure_ascii=False, indent=2)
    )
    (directory / "plans.json").write_text(plans.model_dump_json(indent=2))
    return ReviewedGeneration(
        plans,
        decks,
        results,
        ContentReviewResult.model_validate(contextual),
        VisualReviewResult.model_validate(visual),
        refinement,
    )
