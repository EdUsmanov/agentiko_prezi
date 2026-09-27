"""Regressions for a timeline replaced with a duplicate chart during shortening."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
import json
import pytest

from studio.content import parse_content, parse_constraints
from studio.editorial import EditorialPlan
from studio.editorial_repair import prepare_with_targeted_repairs
from studio.editorial_patch_validation import (
    shortening_contracts,
    validate_contracts,
    validate_repaired_plan,
)
from studio.models import Constraints


def timeline_case():
    source = parse_content(
        "Project milestones.\nIn 2020 the team launched a pilot.\nIn 2021 the team expanded the pilot."
    )
    timeline = {
        "title": "Milestones",
        "purpose": "timeline",
        "bullets": [
            {
                "text": "The team launched a pilot. " * 10,
                "group": "2020",
                "evidence": [{"fact_id": "f2"}],
            },
            {
                "text": "The team expanded the pilot. " * 10,
                "group": "2021",
                "evidence": [{"fact_id": "f3"}],
            },
        ],
    }
    raw = EditorialPlan.model_validate(
        {
            "slides": [
                {
                    "title": "Project",
                    "purpose": "cover",
                    "bullets": [{"text": "Project milestones.", "evidence": [{"fact_id": "f1"}]}],
                },
                timeline,
            ]
        }
    ).model_dump()
    return source, raw


def test_shortening_cannot_switch_subject_or_drop_an_event():
    source, original = timeline_case()
    contract = shortening_contracts(
        original,
        [2],
        {
            "validation": "human-readable explanation can change",
            "repair_issues": [{"code": "text_budget", "slide": 2, "action": "shorten_text"}],
        },
        500,
    )
    changed = deepcopy(original)
    changed["slides"][1]["purpose"] = "trend"
    with pytest.raises(ValueError, match="preserve purpose"):
        validate_contracts(changed, contract)
    changed = deepcopy(original)
    changed["slides"][1]["bullets"].pop()
    with pytest.raises(ValueError, match="retain evidence"):
        validate_contracts(changed, contract)
    changed = deepcopy(original)
    changed["slides"][1]["bullets"][0]["group"] = "2022"
    with pytest.raises(ValueError, match="ordered event"):
        validate_contracts(changed, contract)
    assert source.facts[1].text.endswith("pilot.")


def test_bad_replacement_is_retried_before_caching_or_semantic_review(monkeypatch, tmp_path):
    from studio import narrative

    monkeypatch.setattr(
        narrative,
        "narrative_storyboard",
        lambda p: p.analysis.update(slide_budget={"status": "adjusted"}),
    )
    source, raw = timeline_case()
    package = SimpleNamespace(
        content=source,
        original_content=None,
        template=SimpleNamespace(patterns=[]),
        constraints=Constraints(slides=2, count_mode="exact", summarize=True),
        analysis={},
    )

    class Gateway:
        settings = SimpleNamespace(data_dir=tmp_path)
        calls = []
        requests = 0

        async def json_request(self, stage, payload, **kwargs):
            if stage == "editorial_repair":
                self.requests += 1
                replacement = deepcopy(raw["slides"][1])
                schema = kwargs["schema"]["properties"]["replacements"]["items"]["oneOf"][0]
                assert (
                    schema["properties"]["content"]["properties"]["purpose"]["const"] == "timeline"
                )
                if self.requests == 1:
                    replacement["purpose"] = "trend"
                else:
                    assert "preserve purpose" in payload["validation_feedback"]
                    replacement["bullets"][0]["text"] = "The team launched a pilot."
                    replacement["bullets"][1]["text"] = "The team expanded the pilot."
                return {"replacements": [{"slide": 2, "content": replacement}]}
            assert stage == "editorial_review"
            assert payload["slides"][1]["purpose"] == "timeline"
            return {
                "claims": [
                    {"claim_id": c["claim_id"], "supported": True, "meaning_preserved": True}
                    for c in payload["claims"]
                ],
                "narrative_coherent": True,
            }

    gateway = Gateway()
    assert asyncio.run(prepare_with_targeted_repairs(package, gateway, starting_plan=raw))
    assert gateway.requests == 2
    accepted = package.analysis["editorial"]["plan"]
    assert accepted["slides"][0]["bullets"][0]["text"] == raw["slides"][0]["bullets"][0]["text"]
    assert accepted["slides"][1]["purpose"] == "timeline"
    assert [b["group"] for b in accepted["slides"][1]["bullets"]] == ["2020", "2021"]
    repairs = [
        d
        for path in (tmp_path / "analysis-cache").glob("*.json")
        if "replacements" in (d := json.loads(path.read_text()))
    ]
    assert len(repairs) == 1 and repairs[0]["replacements"][0]["content"]["purpose"] == "timeline"


def test_invalid_chart_rows_are_rejected_immediately_with_specific_feedback():
    source = parse_content("Support was 20% in July 2021 and 18% in November 2021.")
    plan = EditorialPlan.model_validate(
        {
            "slides": [
                {
                    "title": "Support",
                    "purpose": "trend",
                    "bullets": [{"text": "Support declined.", "evidence": [{"fact_id": "f1"}]}],
                    "chart_type": "line",
                    "relationship": "time",
                    "rows": [
                        {"fact_id": "f1", "label": "July 2021", "value": "20%"},
                        {"fact_id": "f1", "label": "Nov 2021", "value": "18%"},
                    ],
                }
            ]
        }
    ).model_dump()
    with pytest.raises(ValueError, match="row 2: label 'Nov 2021'.*verbatim.*f1"):
        validate_repaired_plan(plan, source, (1, 1), 500, False, [1])
    plan["slides"][0]["rows"][1]["label"] = "November 2021"
    validate_repaired_plan(plan, source, (1, 1), 500, False, [1])
    plan["slides"][0]["rows"] = []
    with pytest.raises(ValueError, match="chart needs source data"):
        validate_repaired_plan(plan, source, (1, 1), 500, False, [1])


@pytest.mark.parametrize(
    "instruction",
    ["не менее 5 слайдов", "Не меньше 5 слайдов", "как минимум 5 слайдов", "at least 5 slides"],
)
def test_minimum_slide_count_is_parsed(instruction):
    result = parse_constraints(None, "", instruction, "mini")
    assert result.count_mode == "minimum" and result.slides == 5


def test_minimum_is_part_of_the_editorial_contract_before_requests(monkeypatch):
    from studio import editorial_repair

    package = SimpleNamespace(
        content=parse_content("Source material."),
        original_content=None,
        constraints=parse_constraints(None, "", "не менее 5 слайдов", "mini"),
        template=SimpleNamespace(patterns=[]),
        analysis={},
    )

    class StopAfterContract(Exception):
        pass

    async def check(gateway, stage, payload, schema, validate, **kwargs):
        assert payload["slide_range"] == [5, 5]
        _, four = timeline_case()
        with pytest.raises(ValueError, match="Slide count"):
            validate(four)
        raise StopAfterContract

    monkeypatch.setattr(editorial_repair, "validated_request", check)
    with pytest.raises(StopAfterContract):
        asyncio.run(prepare_with_targeted_repairs(package, SimpleNamespace()))


def test_adjusted_plan_cannot_bypass_minimum():
    from studio.storyboard import planned_slide_count

    package = SimpleNamespace(
        constraints=parse_constraints(None, "", "не менее 5 слайдов", "mini"),
        analysis={
            "slide_budget": {"status": "adjusted", "requested": 5, "planned": 4},
            "storyboard": [{}, {}, {}, {}],
        },
    )
    with pytest.raises(ValueError, match="количества слайдов"):
        planned_slide_count(package)


def test_other_slide_error_does_not_unlock_a_shortening_repair():
    _, original = timeline_case()
    feedback = {
        "validation": "diagnostic wording is not a protocol",
        "repair_issues": [
            {"slide": 1, "code": "unsupported_number", "action": "revise_content"},
            {"slide": 2, "code": "text_budget", "action": "shorten_text"},
        ],
    }
    contracts = shortening_contracts(original, [1, 2], feedback, 500)
    assert [c["slide"] for c in contracts] == [2]
