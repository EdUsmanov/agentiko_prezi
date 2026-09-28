"""Compose, audit and export variants; workers return validated export records."""

import asyncio
from pathlib import Path
from .models import Plans, PreparedPackage
from .audit import audit_scenes, repair_scenes
from .diversity import ensure_diversity
from .render import render_variant
from .stage_results import CompositionResult, VariantResult, DiversityResult
from .stage_runtime import GenerationDeadline


def compose_generation(
    plans: Plans, package: PreparedPackage, directory: Path
) -> CompositionResult:
    from .background_diversity import diversify_backgrounds
    from .composer import CompositionSession

    composition_cache = CompositionSession(package)
    decks = {}
    background_selection = {}
    for i, variant in enumerate(plans.variants):
        selected, scenes, report = diversify_backgrounds(variant, package, composition_cache)
        plans.variants[i] = selected
        decks[selected.key] = scenes
        background_selection[selected.key] = report
    package.analysis["background_diversity"] = background_selection
    initial_by_key = {key: audit_scenes(scenes, package) for key, scenes in decks.items()}
    repairs_by_key = {key: repair_scenes(scenes, package) for key, scenes in decks.items()}
    diversity = ensure_diversity(decks, package)
    from .background_diversity import background_report

    # Diversity can change layouts after the initial background selection.
    # Persist the choices actually exported, not the superseded proposal.
    for variant in plans.variants:
        for slide, scene in zip(variant.slides, decks[variant.key]):
            slide.pattern_id = scene.pattern_id or "token:auto"
            slide.background_pattern_id = scene.background_pattern_id
        report = background_selection[variant.key]
        report["after_selection"] = report["after"]
        report["after"] = background_report(decks[variant.key], package.template)
    (directory / "plans.json").write_text(plans.model_dump_json(indent=2))

    return CompositionResult(
        plans,
        decks,
        initial_by_key,
        repairs_by_key,
        DiversityResult.model_validate(diversity),
        composition_cache.hits,
        composition_cache.misses,
    )


async def export_variants(
    composition: CompositionResult,
    package: PreparedPackage,
    source: Path,
    directory: Path,
    deadline: GenerationDeadline,
) -> list[VariantResult]:
    def build(variant) -> VariantResult:
        deadline.remaining()
        scenes = composition.decks[variant.key]
        initial = composition.initial_findings[variant.key]
        repairs = composition.repairs[variant.key]
        findings = audit_scenes(scenes, package)
        deadline.remaining()
        rendering = render_variant(scenes, package.template, source, directory / variant.key)
        from .export_audit import audit_export

        return VariantResult.model_validate(
            {
                "key": variant.key,
                "title": variant.title,
                "slides": len(scenes),
                "export_findings": audit_export(
                    directory / variant.key / "deck.pptx", variant, package
                ),
                "rendering": rendering,
                "template_strategies": sorted({s.strategy for s in scenes}),
                "findings": [f.model_dump() for f in findings],
                "repairs": [f.model_dump() for f in repairs],
                "initial_errors": sum(f.severity == "error" for f in initial),
            }
        )

    return list(
        await asyncio.gather(*(asyncio.to_thread(build, v) for v in composition.plans.variants))
    )
