import json
from pathlib import Path

from audit_e2e.defect_corpus import _read_registry, _score_validation


EXPECTED_CATEGORIES = {
    "omission",
    "number",
    "negation",
    "condition",
    "table_binding",
    "units",
    "provenance",
    "hidden_text",
    "duplicates",
    "template_fidelity",
    "readability",
    "clipping",
    "overlap",
    "lowcontrast",
    "backgroundloss",
    "foreign_template_content",
    "chart_value",
    "chart_axis",
    "image_missing",
    "image_distorted",
    "pptx_pdf_mismatch",
    "pptx_html_mismatch",
}
VARIANTS = {"executive", "analytical", "story"}


def _record(case_id, status, findings=()):
    return {"case_id": case_id, "audit": {"status": status, "findings": list(findings)}}


def _localized_variant(variant, status="failed"):
    return {"variant": variant, "status": status}


def test_owned_fixture_is_complete_and_source_anchored():
    registry = _read_registry()
    assert {item["id"] for item in registry["categories"]} == EXPECTED_CATEGORIES
    assert len({item["id"] for item in registry["sources"]}) == 3
    assert len(registry["cosmetic_controls"]) >= 3
    assert len(registry["source_limited"]) >= 3
    for source in registry["sources"]:
        for point in source["facts"]:
            assert point["quote"] in point["statement"]


def test_score_requires_failed_evidence_at_every_expected_variant():
    label = {
        "case_id": "partial",
        "expected_status": "failed",
        "expected_category": "number",
        "expected_localization": {name: {} for name in VARIANTS},
    }
    finding = {
        "category": "number",
        "severity": "failed",
        "evidence": {
            "variant": "executive",
            "assessment": {
                "findings": [
                    _localized_variant("analytical"),
                    _localized_variant("story", "inconclusive"),
                ]
            },
        },
    }
    result = _score_validation([_record("partial", "failed", [finding])], [label])
    case = result["cases"][0]
    assert case["outcome"] == "inconclusive"
    assert set(case["failed_target_variants"]) == {"executive", "analytical"}
    assert not case["all_expected_variant_localizations_present"]

    finding["evidence"]["assessment"]["findings"].append(_localized_variant("story"))
    result = _score_validation([_record("partial", "failed", [finding])], [label])
    assert result["cases"][0]["outcome"] == "detected"


def test_expected_inconclusive_labels_require_an_inconclusive_audit():
    label = {
        "case_id": "limited",
        "expected_status": "inconclusive",
        "expected_category": "source_limited",
        "expected_localization": {},
    }
    labels = [label]
    assert _score_validation([_record("limited", "inconclusive")], labels)["cases"][0]["outcome"] == "inconclusive"
    assert _score_validation([_record("limited", "failed", [{"category": "other", "severity": "failed"}])], labels)["cases"][0]["outcome"] == "false_positive"
    assert _score_validation([_record("limited", "passed")], labels)["cases"][0]["outcome"] == "false_success"


def test_diversity_mapping_needs_all_three_failed_pairs():
    label = {
        "case_id": "cosmetic",
        "expected_status": "failed",
        "expected_category": "presentation_diversity",
        "expected_localization": {name: {} for name in VARIANTS},
    }
    pairs = [
        {"variants": ["executive", "analytical"], "status": "failed"},
        {"variants": ["executive", "story"], "status": "failed"},
        {"variants": ["analytical", "story"], "status": "inconclusive"},
    ]
    finding = {
        "category": "presentation_diversity",
        "severity": "failed",
        "evidence": {"assessment": {"pairs": pairs}},
    }
    row = _record("cosmetic", "failed", [finding])
    assert _score_validation([row], [label])["cases"][0]["outcome"] == "inconclusive"
    finding["evidence"]["assessment"]["pairs"][-1]["status"] = "failed"
    assert _score_validation([row], [label])["cases"][0]["outcome"] == "detected"


def test_duplicate_detection_needs_every_pair():
    label = {
        "case_id": "duplicates",
        "expected_status": "failed",
        "expected_category": "duplicates",
        "expected_localization": {name: {} for name in VARIANTS},
    }
    finding = {
        "category": "duplicates",
        "severity": "failed",
        "evidence": {"pairs": [["executive", "analytical"], ["executive", "story"]]},
    }
    row = _record("duplicates", "failed", [finding])
    assert _score_validation([row], [label])["cases"][0]["outcome"] == "inconclusive"
    finding["evidence"]["pairs"].append(["analytical", "story"])
    assert _score_validation([row], [label])["cases"][0]["outcome"] == "detected"


def test_historical_observation_index_is_metadata_only():
    path = Path(__file__).parents[1] / "fixtures" / "historical_observations.json"
    index = json.loads(path.read_text(encoding="utf-8"))
    assert index["classification"] == "local_only_historical_observations_not_calibration_labels"
    assert all("sha256" in item or "judge_description" in item for item in index["observations"][0]["artifacts"].values())
    assert "overall_run_inconclusive" in index["observations"][0]["evidence_status"]
