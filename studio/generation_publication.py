"""Publish only after the quality gate; serialize existing manifest/API shapes here."""

import json
import time
from pathlib import Path
from .artifacts import package_results
from .models import PreparedPackage
from .config import Settings
from .gateway import ModelGateway
from .store import Store
from .stage_results import (
    PlanningResult,
    CompositionResult,
    ReviewedGeneration,
    FinalAuditResult,
    JsonObject,
)
from .stage_runtime import GenerationDeadline


def publish_generation(
    store: Store,
    job_id: str,
    job: JsonObject,
    package: PreparedPackage,
    settings: Settings,
    gateway: ModelGateway,
    directory: Path,
    deadline: GenerationDeadline,
    planning: PlanningResult,
    composition: CompositionResult,
    reviewed: ReviewedGeneration,
    audit: FinalAuditResult,
    timings: dict[str, float],
    version_snapshot: dict[str, str],
    git_revision: str,
) -> None:
    results = [r.wire() for r in audit.variants]
    contextual, visual = reviewed.contextual.wire(), reviewed.visual.wire()
    refinement, engine_report = reviewed.refinement.wire(), planning.engine.wire()
    planning_source = planning.source
    diversity, warnings = audit.diversity.wire(), audit.warnings
    font_substitutions, errors = audit.font_substitutions, audit.errors
    native_preview, model_degraded = audit.native_preview, audit.model_degraded
    manifest = {
        "run_id": job_id,
        "package_id": package.id,
        "package_hash": store.get(package.id)["package_hash"],
        "input_manifest": package.manifest,
        "generation_versions": version_snapshot,
        "git_commit": git_revision,
        "model_proposal_count": sum(f.source == "model_proposal" for f in package.content.facts),
        "model": {
            "mode": settings.mode,
            "id": settings.model_id or None,
            "parameters_b": settings.parameters_b,
            "license": settings.license,
            "stage": settings.stage,
            "usage": gateway.usage,
            "thinking_requested": settings.thinking,
            "structured_output": settings.structured_output,
            "calls": gateway.calls,
        },
        "model_degraded": model_degraded,
        "engine": engine_report,
        "planning_source": planning_source,
        "composition_diversity": diversity,
        "preparation_analysis": package.analysis,
        "font_substitutions": font_substitutions,
        "started_at": job["created"],
        "deadline_at": deadline.deadline_at,
        "variant_count": len(results),
        "variants": results,
        "contextual_audit": contextual,
        "visual_audit": visual,
        "refinement": refinement,
        "warnings": warnings,
        "errors": errors,
        "checks": {
            "native_pptx_reopened": True,
            "pdf_pages": True,
            "html_live_dom": True,
            "native_pptx_render": native_preview,
            "powerpoint_visual_check": False,
            "ocr_check": False,
        },
    }
    from .diagnostics import stage_summary

    manifest["timings"] = {
        **timings,
        "model": stage_summary(gateway.calls),
        "composition_cache": {"hits": composition.cache_hits, "misses": composition.cache_misses},
    }
    from .quality import quality_report

    manifest["quality_report"] = quality_report(manifest)
    store.update(
        job_id, phase="Упаковываем готовые презентации и отчёт для скачивания", progress=96
    )
    publish_artifacts(directory, manifest, deadline, job["created"], settings.deadline_seconds)
    needs_review = manifest["quality_report"]["status"] != "passed_checks"
    store.update(
        job_id,
        "needs_review" if needs_review else "completed",
        phase="Требуется проверка"
        if needs_review
        else ("Презентация готова" if len(results) == 1 else "Три презентации готовы"),
        progress=100,
        elapsed_seconds=round(time.time() - job["created"], 3),
        analysis_seconds=package.manifest.get("analysis_seconds"),
        variants=results,
        warnings=warnings,
        font_substitutions=font_substitutions,
        contextual_audit=contextual,
        visual_audit=visual,
        errors=errors,
        within_deadline=True,
        model_mode=settings.mode,
        refinement=refinement,
        quality_report=manifest["quality_report"],
        native_pptx_render=native_preview,
        composition_diversity=diversity,
        model_degraded=model_degraded,
        planning_source=manifest["planning_source"],
        engine=engine_report,
    )


def publish_artifacts(
    directory: Path,
    manifest: JsonObject,
    deadline: GenerationDeadline,
    job_created: float,
    deadline_seconds: float | None,
) -> None:
    """Retain failure evidence, but build downloadable archives only after the gate."""
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    from .quality_gate import require_publishable

    require_publishable(manifest)
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    package_results(directory)
    deadline.remaining()
    elapsed = time.time() - job_created
    manifest["elapsed_seconds"] = round(elapsed, 3)
    manifest["elapsed_scope"] = (
        "through_first_zip; API elapsed_seconds includes final repack and is authoritative"
    )
    manifest["within_deadline"] = deadline_seconds is None or elapsed <= deadline_seconds
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    # Repack to include final measured manifest; final API elapsed includes this too.
    package_results(directory)
    deadline.remaining()
