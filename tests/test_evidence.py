from studio.composition.evidence import write_evidence
from studio.checks.quality_gate import require_publishable


def test_evidence_is_standalone_and_claims_only_recorded_checks(tmp_path):
    variants = []
    for key in ("executive", "analytical", "story"):
        folder = tmp_path / key
        folder.mkdir()
        (folder / "slide-1.png").write_bytes(b"png")
        (folder / "slides.json").write_text(
            '[{"title":"Факт","pattern_id":"native-1","source_ids":["f1"],"elements":[{"kind":"text"}]}]'
        )
        variants.append({"key": key, "title": key, "slides": 1})
    manifest = {
        "run_id": "abc",
        "variants": variants,
        "quality_report": {"status": "passed_checks", "errors": 0, "warnings": 0, "findings": []},
        "checks": {"native_pptx_reopened": True, "pdf_pages": True, "html_live_dom": True},
        "composition_diversity": {"verified": True},
    }
    html = write_evidence(tmp_path, manifest).read_text()
    assert html.count("data:image/png;base64") == 3
    assert "native-1" in html and "f1" in html
    assert "требует сравнения трёх отдельных запусков" in html
    assert "не реализована" in html
    assert "http://" not in html and "https://" not in html


def test_gate_keeps_repair_comparison_for_published_evidence():
    manifest = {
        "variants": [],
        "visual_audit": {"status": "completed", "total": 0, "checked": 0},
        "contextual_audit": {"status": "completed"},
        "checks": {"native_pptx_render": True},
        "quality_report": {},
    }
    require_publishable(manifest)
    manifest["quality_report"]["repair_comparison"] = {
        "selected_still_present": ["finding-a"],
        "selected_not_observed": [],
        "new": [],
    }
    require_publishable(manifest)
    assert manifest["quality_report"]["repair_comparison"]["selected_still_present"] == [
        "finding-a"
    ]
