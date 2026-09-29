"""Boundary failures must stop before repair/publication, without changing saved JSON."""

import pytest
from pydantic import ValidationError
from studio.generation.results import (
    ContentReviewResult,
    EngineReport,
    RefinementReport,
    RenderingResult,
    VariantResult,
    VisualReviewResult,
)
from studio.stage_runtime import GenerationDeadline


def variant_payload():
    return {
        "key": "executive",
        "title": "Summary",
        "slides": 2,
        "rendering": {
            "native_render": True,
            "preview_source": "libreoffice_pptx",
            "object_repairs": [
                {"code": "pptx_text_fit", "slide": 1, "message": "Fit", "scale": 0.9}
            ],
        },
        "template_strategies": ["native_template"],
        "findings": [{"code": "readability", "severity": "warning", "message": "Check"}],
        "repairs": [],
        "initial_errors": 0,
    }


def test_variant_wire_preserves_optional_keys_and_object_repairs():
    payload = variant_payload()
    result = VariantResult.model_validate(payload)
    assert result.rendering.object_repairs[0].scale == 0.9
    assert result.wire() == payload
    assert VariantResult.model_validate(result.wire()).wire() == payload


def test_export_findings_are_repair_evidence_not_duplicate_public_findings():
    payload = variant_payload()
    payload["export_findings"] = [
        {"code": "pptx_overflow", "severity": "error", "message": "Overflow", "slide": 1}
    ]
    result = VariantResult.model_validate(payload)
    assert result.export_findings[0].code == "pptx_overflow"
    assert "export_findings" not in result.wire()
    assert result.review_input()["export_findings"][0]["code"] == "pptx_overflow"
    assert len(result.wire()["findings"]) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"key": "unknown"},
        {"slides": 0},
        {"slides": "2"},
        {"initial_errors": -1},
        {"rendering": {"native_render": True}},
        {"findings": [{"code": "x", "severity": "success", "message": "x"}]},
        {"typo_in_stage_field": True},
    ],
)
def test_invalid_export_contract_is_rejected_at_boundary(change):
    with pytest.raises(ValidationError):
        VariantResult.model_validate({**variant_payload(), **change})


def test_native_render_flag_cannot_be_truthy_text_and_assignment_is_checked():
    with pytest.raises(ValidationError):
        RenderingResult(preview_source="scene_model", native_render="false")
    result = RenderingResult(preview_source="scene_model", native_render=False)
    with pytest.raises(ValidationError):
        result.native_render = "true"
    assert result.native_render is False


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (ContentReviewResult, {"status": "not_run", "reason": "offline", "findings": []}),
        (EngineReport, {"engine": "native", "status": "completed"}),
        (RefinementReport, {"status": "not_needed", "attempts": 0, "accepted": False, "edits": []}),
        (
            VisualReviewResult,
            {
                "status": "failed",
                "checked": 1,
                "total": 2,
                "findings": [],
                "batches": [],
                "model": "test",
                "method": "rendered_pptx_images",
                "reason": "TimeoutError",
                "seconds": 1.2,
            },
        ),
    ],
)
def test_review_roundtrip_preserves_not_run_and_partial_failure(model, payload):
    assert model.model_validate(payload).wire() == payload
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "status": "looks_ok"})


def test_deadline_reserves_export_time_and_preserves_unlimited_mode(monkeypatch):
    monkeypatch.setattr("studio.stage_runtime.time.time", lambda: 100)
    assert GenerationDeadline(None).remaining(150) == float("inf")
    assert GenerationDeadline(200).remaining(30) == 70
    with pytest.raises(ValueError, match="обязательного экспорта"):
        GenerationDeadline(120).remaining(30)
    with pytest.raises(TimeoutError):
        GenerationDeadline(99).remaining()


def test_new_stage_boundary_does_not_migrate_or_discard_package_diagnostics(prepared):
    from studio.models import PreparedPackage
    from studio.pipeline import load_package
    from studio.security import digest

    _, store, package = prepared
    package.analysis["historical_diagnostic"] = {"unknown_old_field": [1, "kept"]}
    raw = package.model_dump_json(indent=2)
    path = store.directory(package.id) / "package.json"
    path.write_text(raw)
    store.update(package.id, package_hash=digest(raw.encode()))
    loaded = load_package(store, package.id)
    assert loaded.analysis == package.analysis
    assert PreparedPackage.model_validate_json(raw).manifest == loaded.manifest
    # Verification must continue to cover the ORIGINAL bytes, not normalized JSON.
    path.write_text(raw + " ")
    with pytest.raises(ValueError, match="изменён после анализа"):
        load_package(store, package.id)


@pytest.mark.parametrize("checked,total", [(1, 2), (3, 2)])
def test_visual_completion_requires_exact_coverage(checked, total):
    with pytest.raises(ValidationError, match="every rendered slide"):
        VisualReviewResult(
            status="completed",
            findings=[],
            checked=checked,
            total=total,
            batches=[],
            model="test",
            method="rendered_pptx_images",
        )


@pytest.mark.parametrize(
    "status,accepted,edits",
    [("failed", True, []), ("completed", False, []), ("completed", True, [])],
)
def test_refinement_cannot_claim_success_without_accepted_edits(status, accepted, edits):
    with pytest.raises(ValidationError, match="accepted refinement"):
        RefinementReport(status=status, attempts=1, accepted=accepted, edits=edits)


def test_publication_gate_failure_preserves_diagnostics_but_never_packages(tmp_path, monkeypatch):
    import json
    from studio.generation.publication import publish_artifacts

    calls = []

    def reject(manifest):
        calls.append("gate")
        raise ValueError("blocked")

    monkeypatch.setattr("studio.checks.quality_gate.require_publishable", reject)
    monkeypatch.setattr(
        "studio.generation.publication.package_results", lambda _: calls.append("zip")
    )
    with pytest.raises(ValueError, match="blocked"):
        publish_artifacts(tmp_path, {"errors": 1}, GenerationDeadline(None), 0, None)
    assert calls == ["gate"]
    assert json.loads((tmp_path / "manifest.json").read_text()) == {"errors": 1}


def test_publication_orders_gate_and_both_archives_before_completion(tmp_path, monkeypatch):
    from studio.generation.publication import publish_artifacts

    calls = []
    monkeypatch.setattr(
        "studio.checks.quality_gate.require_publishable", lambda _: calls.append("gate")
    )
    monkeypatch.setattr(
        "studio.generation.publication.package_results", lambda _: calls.append("zip")
    )
    monkeypatch.setattr("studio.generation.publication.time.time", lambda: 10)
    manifest = {"errors": 0}
    publish_artifacts(tmp_path, manifest, GenerationDeadline(None), 5, None)
    assert calls == ["gate", "zip", "zip"]
    assert manifest["elapsed_seconds"] == 5
    assert manifest["within_deadline"] is True


def test_final_quality_error_still_packages_reviewable_exports(tmp_path, monkeypatch):
    import json
    from studio.generation.publication import publish_artifacts

    archives = []
    monkeypatch.setattr(
        "studio.generation.publication.package_results", lambda _: archives.append("zip")
    )
    manifest = {
        "errors": 1,
        "variants": [
            {
                "key": "executive",
                "findings": [
                    {
                        "severity": "error",
                        "code": "coverage",
                        "slide": 1,
                        "message": "Факт отсутствует на слайде",
                    }
                ],
            }
        ],
    }

    publish_artifacts(tmp_path, manifest, GenerationDeadline(None), 0, None)

    assert archives == ["zip", "zip"]
    saved = json.loads((tmp_path / "manifest.json").read_text())
    assert saved["quality_report"]["status"] == "needs_review"
    assert saved["quality_report"]["errors"] == 1
    assert saved["quality_report"]["findings"][0]["code"] == "coverage"
