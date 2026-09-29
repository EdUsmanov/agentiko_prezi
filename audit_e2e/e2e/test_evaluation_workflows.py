"""Real-browser evaluation paths backed only by reviewed finite replay cassettes."""

import json

from audit_e2e.corpus import load_cases, materialize_case
from audit_e2e.runtime import execute_case
from audit_e2e.reporting import AUDIT_ROOT


CASSETTES = AUDIT_ROOT / "fixtures/cassettes"


def _case(case_id, directory):
    case = next(case for case in load_cases("extended") if case["id"] == case_id)
    materialized = materialize_case(case, directory / "input", synthetic=True)
    materialized["cassette"] = CASSETTES / f"{case_id}.json"
    return materialized


def test_brief_requires_real_ui_approval_before_one_generation(tmp_path):
    case = _case("binghamton-brief", tmp_path)
    result_dir = tmp_path / "brief-result"
    result = execute_case(case, result_dir, mode="replay", timeout=600)

    assert result["status"] == "passed", result
    assert result["model_requests"] == 0
    assert result["generate_requests"] <= 1
    assert result["generation_jobs"] and len(result["generation_jobs"]) == 1
    draft = json.loads((result_dir / "draft.json").read_text())
    approval = json.loads((result_dir / "approval.json").read_text())
    denied = json.loads((result_dir / "preapproval-generation.json").read_text())
    assert denied["status"] == 409
    assert approval["approved_package_hash"] == draft["package_hash"]
    assert approval["approved_draft_hash"] == draft["draft_hash"]
    assert result["source_unchanged"] is True


def test_selected_repair_uses_one_real_ui_selection_and_preserves_other_slides(tmp_path):
    case = _case("binghamton-content", tmp_path)
    case.update(
        id="browser-selected-repair",
        interface="browser",
        selected_repair=True,
        cassette=CASSETTES / "browser-selected-repair.json",
    )
    result_dir = tmp_path / "repair-result"
    result = execute_case(case, result_dir, mode="replay", timeout=600)

    assert result["status"] == "passed", result
    assert result["model_requests"] == 0
    assert result["repair_requests"] == 1
    assert result["selected_repair"]["comparison"]["untouched_slides_unchanged"] is True
    assert result["selected_repair"]["comparison"]["untouched_slides_compared"] > 0
    assert result["source_unchanged"] is True
