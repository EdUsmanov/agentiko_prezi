"""Publish only after the quality gate; serialize existing manifest/API shapes here."""

import json
import time
from pathlib import Path
from studio.composition.artifacts import package_results
from studio.models import PreparedPackage, JsonObject
from studio.config import Settings
from studio.providers.gateway import ModelGateway
from studio.jobs.store import Store
from studio.generation.results import (
    PlanningResult,
    CompositionResult,
    ReviewedGeneration,
    FinalAuditResult,
)
from studio.stage_runtime import GenerationDeadline


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
    resource_kinds = {resource.id: resource.kind for resource in package.template.resources}
    device_ids = {image.id for image in package.images if image.presentation == "device"}
    resources_used = []
    device_fallbacks = []
    for key, scenes in reviewed.decks.items():
        for number, scene in enumerate(scenes, 1):
            wrapped = set()
            for element in scene.elements:
                if element.resource_id:
                    kind = resource_kinds.get(element.resource_id, "unknown")
                    resources_used.append(
                        {
                            "variant": key,
                            "slide": number,
                            "resource_id": element.resource_id,
                            "kind": kind,
                        }
                    )
                    if kind == "device_frame":
                        wrapped.add(element.field_style.get("paired_image_id"))
            for element in scene.elements:
                if element.image_id in device_ids and element.image_id not in wrapped:
                    device_fallbacks.append(
                        {
                            "variant": key,
                            "slide": number,
                            "image_id": element.image_id,
                            "reason": "Нет подходящей отделимой рамки или места",
                        }
                    )
    manifest = {
        "run_id": job_id,
        "package_id": package.id,
        "package_hash": store.get(package.id)["package_hash"],
        "input_manifest": package.manifest,
        "generation_versions": version_snapshot,
        "git_commit": git_revision,
        "model_proposal_count": (
            sum(b.proposed for s in package.draft.slides for b in s.bullets)
            if package.draft
            else sum(f.source == "model_proposal" for f in package.content.facts)
        ),
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
        "resource_usage": {"used": resources_used, "device_fallbacks": device_fallbacks},
        "run_provenance": {
            "generation": "extractive"
            if settings.mode == "extractive"
            else getattr(settings, "execution_kind", "unverified")
            if gateway.calls
            else "unverified",
            "preparation": package.manifest.get("execution_kind")
            or ("extractive" if package.control.model_mode == "extractive" else "unverified"),
        },
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
    from studio.diagnostics import stage_summary

    manifest["timings"] = {
        **timings,
        "model": stage_summary(gateway.calls),
        "composition_cache": {"hits": composition.cache_hits, "misses": composition.cache_misses},
    }
    from studio.checks.quality import quality_report

    manifest["quality_report"] = quality_report(manifest)
    if job.get("parent_generation_id"):
        from collections import Counter
        from studio.checks.review_snapshot import load_snapshot

        previous = load_snapshot(store, job["parent_generation_id"])

        def identity(f):
            return (
                f.get("source"),
                f.get("variant"),
                f.get("slide"),
                f.get("code"),
                f.get("element"),
                f.get("message"),
                f.get("severity"),
            )

        old_findings = [f for f in previous.findings if not f.repaired]
        now_rows = [
            f for f in manifest["quality_report"]["findings"] if f.get("source") != "repair"
        ]
        new_counter = Counter(identity(f) for f in now_rows)
        persisted, resolved = [], []
        for finding in old_findings:
            key = identity(finding.model_dump())
            if new_counter[key]:
                persisted.append(finding.id)
                new_counter[key] -= 1
            else:
                resolved.append(finding.id)
        additions = []
        for row in now_rows:
            key = identity(row)
            if new_counter[key]:
                additions.append(
                    {
                        k: row.get(k)
                        for k in (
                            "source",
                            "variant",
                            "slide",
                            "code",
                            "element",
                            "message",
                            "severity",
                        )
                    }
                )
                new_counter[key] -= 1
        selected_ids = set(job.get("finding_ids", []))

        def coarse(f):
            return (f.get("source"), f.get("variant"), f.get("slide"), f.get("code"))

        after_codes = Counter(coarse(f) for f in now_rows)
        selected_still_present = [
            f.id
            for f in old_findings
            if f.id in selected_ids and after_codes[coarse(f.model_dump())] > 0
        ]
        selected_not_observed = [
            f.id
            for f in old_findings
            if f.id in selected_ids and after_codes[coarse(f.model_dump())] == 0
        ]
        manifest["quality_report"]["repair_comparison"] = {
            "resolved": resolved,
            "persisted": persisted,
            "new": additions,
            "selected_not_observed": selected_not_observed,
            "selected_still_present": selected_still_present,
            "method": "source_variant_slide_code_element_message_severity_multiset",
            "selected_method": "conservative_source_variant_slide_code_presence",
        }
    # This is the post-automatic-repair state. Keep it even if the publication
    # gate rejects the draft; public downloads remain closed for failed jobs.
    from studio.checks.review_snapshot import build_snapshot, save_snapshot

    (directory / "audit-input.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    audit_snapshot = build_snapshot(store, job_id, manifest, package, reviewed.plans)
    audit_hash = save_snapshot(store, audit_snapshot)
    store.update(job_id, review_available=True, audit_hash=audit_hash)
    store.update(
        job_id, phase="Упаковываем готовые презентации и отчёт для скачивания", progress=96
    )
    publish_artifacts(
        directory, manifest, deadline, job["created"], settings.deadline_seconds, package
    )
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
        resource_usage=manifest["resource_usage"],
        planning_source=manifest["planning_source"],
        engine=engine_report,
    )


def publish_artifacts(
    directory: Path,
    manifest: JsonObject,
    deadline: GenerationDeadline,
    job_created: float,
    deadline_seconds: float | None,
    package: PreparedPackage | None = None,
) -> None:
    """Retain failure evidence, but build downloadable archives only after the gate."""
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    from studio.checks.quality_gate import require_publishable

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
    from studio.composition.evidence import write_evidence

    if package is not None:
        write_evidence(directory, manifest, package)
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    # Repack to include final measured manifest; final API elapsed includes this too.
    package_results(directory)
    deadline.remaining()
