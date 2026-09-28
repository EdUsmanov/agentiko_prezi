"""Presentation numbering must not become unsupported data or hide real data."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from studio.content import parse_content
from studio.editorial_domain import validate_plan
from studio.editorial_patch_validation import numeric_evidence_hints
from studio.editorial_repair import prepare_with_targeted_repairs
from studio.models import Constraints


def process_case(label="Шаг"):
    content = parse_content("Соберите требования. Реализуйте решение. Проверьте результат.")
    raw = {
        "slides": [
            {
                "title": "Порядок работы",
                "purpose": "process",
                "bullets": [
                    {
                        "text": fact.text,
                        "group": f"{label} {index}",
                        "evidence": [{"fact_id": fact.id}],
                    }
                    for index, fact in enumerate(content.facts, 1)
                ],
            }
        ]
    }
    return content, raw


@pytest.mark.parametrize("label", ["Шаг", "Этап", "Пункт", "Step", "Stage", "Item"])
def test_positional_process_labels_preserve_content_and_need_no_numeric_repair(label):
    content, raw = process_case(label)
    # Terminal punctuation and whitespace do not change presentation numbering.
    raw["slides"][0]["bullets"][1]["group"] += ":"
    raw["slides"][0]["bullets"][2]["group"] = f"  {label.upper()} 3. "
    snapshot = deepcopy(raw)
    accepted = validate_plan(raw, content, (1, 1))
    assert raw == snapshot
    assert [b["group"] for b in accepted["slides"][0]["bullets"]] == [
        b["group"] for b in raw["slides"][0]["bullets"]
    ]
    assert numeric_evidence_hints(raw, [1], content) == []


@pytest.mark.parametrize(
    "label",
    ["Шаг 4", "Шаг 2026", "Шаг 1: рост 50%", "Спринт 1", "2026", "Бюджет 100", "Шаг -1"],
)
def test_descriptive_or_nonpositional_labels_still_require_evidence(label):
    content, raw = process_case()
    raw["slides"][0]["bullets"][0]["group"] = label
    with pytest.raises(ValueError, match="Unsupported number"):
        validate_plan(raw, content, (1, 1))
    assert numeric_evidence_hints(raw, [1], content)[0]["claim_id"] == "s1b1"


@pytest.mark.parametrize("purpose", ["timeline", "content", "comparison", "structure"])
def test_numbering_exception_is_limited_to_process(purpose):
    content, raw = process_case()
    raw["slides"][0]["purpose"] = purpose
    with pytest.raises(ValueError, match="Unsupported number"):
        validate_plan(raw, content, (1, 1))
    assert len(numeric_evidence_hints(raw, [1], content)) == 3


@pytest.mark.parametrize(
    "extra,number",
    [
        (" За 1 день.", "1"),
        (" Рост 50%.", "50"),
        (" Бюджет 100 рублей.", "100"),
        (" В 2026 году.", "2026"),
        (" Первый запуск.", "1"),
    ],
)
def test_positional_label_does_not_exempt_numbers_in_claim(extra, number):
    content, raw = process_case()
    raw["slides"][0]["bullets"][0]["text"] += extra
    with pytest.raises(ValueError, match="Unsupported number"):
        validate_plan(raw, content, (1, 1))
    hints = numeric_evidence_hints(raw, [1], content)
    assert len(hints) == 1 and hints[0]["unsupported_numbers"] == [number]


def test_process_title_still_requires_its_own_numeric_evidence():
    content, raw = process_case()
    raw["slides"][0]["title"] = "Результат за 12 недель"
    with pytest.raises(ValueError, match="title: Unsupported number.*12"):
        validate_plan(raw, content, (1, 1))


def test_numbering_does_not_allow_forged_citation():
    content, raw = process_case()
    raw["slides"][0]["bullets"][0]["evidence"][0]["quote"] = "Нет в источнике"
    with pytest.raises(ValueError, match="quote"):
        validate_plan(raw, content, (1, 1))


def test_targeted_repair_preserves_numbering_and_runs_semantic_review(monkeypatch, tmp_path):
    from studio import narrative_layout

    monkeypatch.setattr(
        narrative_layout,
        "narrative_storyboard",
        lambda package: package.analysis.update(slide_budget={"status": "adjusted"}),
    )
    content, correct = process_case()
    initial = deepcopy(correct)
    initial["slides"][0]["bullets"][0]["text"] += " За 2 дня."
    package = SimpleNamespace(
        content=content,
        original_content=None,
        template=SimpleNamespace(patterns=[]),
        constraints=Constraints(slides=1, count_mode="exact", summarize=True),
        analysis={},
    )

    class Gateway:
        settings = SimpleNamespace(data_dir=tmp_path)

        def __init__(self):
            self.stages = []

        async def json_request(self, stage, payload, **kwargs):
            self.stages.append(stage)
            if stage == "editorial_repair":
                assert len(payload["numeric_evidence_hints"]) == 1
                assert payload["numeric_evidence_hints"][0]["unsupported_numbers"] == ["2"]
                return {"replacements": [{"slide": 1, "content": correct["slides"][0]}]}
            assert stage == "editorial_review"
            return {
                "claims": [
                    {"claim_id": c["claim_id"], "supported": True, "meaning_preserved": True}
                    for c in payload["claims"]
                ],
                "narrative_coherent": True,
            }

    gateway = Gateway()
    assert asyncio.run(prepare_with_targeted_repairs(package, gateway, starting_plan=initial))
    assert gateway.stages == ["editorial_repair", "editorial_review"]
    assert len(package.analysis["editorial"]["repair_history"]) == 1
    assert [fact.text for fact in package.original_content.facts] == [f.text for f in content.facts]
