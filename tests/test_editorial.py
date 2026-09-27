import asyncio
from copy import deepcopy
from types import SimpleNamespace
import pytest
from studio.content import parse_content
from studio.editorial import validate_plan, validate_review, apply_plan, prepare_editorial
from studio.models import Constraints


def text_fit_issue():
    return {
        "slide": 1,
        "fields": [{"target_max_characters": 28, "role": "body", "fact_ids": ["summary-1-1"]}],
        "repair_issues": [
            {
                "code": "text_overflow",
                "action": "shorten_text",
                "slide": 1,
                "element": 0,
                "message": "Card too small",
            }
        ],
    }


def source():
    return parse_content(
        "Проект ускорит обработку заявок. Пилот не гарантирует экономии. Встреча пройдёт в комнате 7."
    )


def plan():
    return {
        "slides": [
            {
                "title": "Задача пилота",
                "purpose": "content",
                "bullets": [
                    {
                        "text": "Цель — ускорить обработку заявок.",
                        "group": "Цель",
                        "evidence": [
                            {"fact_id": "f1", "quote": "Проект ускорит обработку заявок."}
                        ],
                    },
                    {
                        "text": "Экономия на пилоте не гарантирована.",
                        "group": "Ограничение",
                        "evidence": [{"fact_id": "f2", "quote": "Пилот не гарантирует экономии."}],
                    },
                ],
            }
        ],
        "omitted": [
            {
                "fact_id": "f3",
                "reason": "detail",
                "explanation": "Организация встречи не влияет на решение руководителя.",
            }
        ],
    }


def test_selection_can_omit_secondary_numeric_detail_without_losing_original():
    c = source()
    raw = validate_plan(plan(), c, (1, 1))
    p = SimpleNamespace(content=c, original_content=c.model_copy(deep=True), analysis={})
    apply_plan(p, raw, {}, (1, 1))
    assert len(p.content.facts) == 2 and len(p.original_content.facts) == 3
    assert "комнате 7" in p.analysis["editorial"]["omitted"][0]["source_text"]
    assert p.analysis["editorial"]["provenance"][0]["evidence"][0]["fact_id"] == "f1"


def test_exact_count_is_binding_and_every_omission_is_accounted_for():
    with pytest.raises(ValueError, match="Count"):
        validate_plan(plan(), source(), (2, 2))
    raw = plan()
    raw["omitted"] = []
    assert validate_plan(raw, source(), (1, 1))["omitted"][0]["fact_id"] == "f3"
    raw = plan()
    raw["omitted"][0]["fact_id"] = "f1"
    accepted = validate_plan(raw, source(), (1, 1))
    assert [o["fact_id"] for o in accepted["omitted"]] == ["f3"]
    assert raw["omitted"][0]["fact_id"] == "f1"  # No mutation of the model reply.
    from studio.editorial import review_payload

    payload = review_payload(accepted, source())
    assert len(payload["source"]["facts"]) == 3
    assert payload["omitted"][0]["fact_id"] == "f3"


def test_synthesis_rejects_invented_number_and_forged_citation():
    raw = plan()
    raw["slides"][0]["bullets"][0]["text"] = "Экономия 50%."
    with pytest.raises(ValueError, match="number"):
        validate_plan(raw, source(), (1, 1))
    raw = plan()
    raw["slides"][0]["bullets"][0]["evidence"][0]["quote"] = "Чужой текст"
    with pytest.raises(ValueError, match="quote"):
        validate_plan(raw, source(), (1, 1))


def test_review_cannot_skip_a_claim_or_invent_missing_evidence():
    raw = {
        "claims": [{"claim_id": "s1b1", "supported": True, "meaning_preserved": True}],
        "narrative_coherent": True,
    }
    with pytest.raises(ValueError, match="every claim"):
        validate_review(raw, ["s1b1", "s1b2"], ["f1"])
    raw["missing_essential_fact_ids"] = ["f99"]
    with pytest.raises(ValueError, match="Unknown"):
        validate_review(raw, ["s1b1"], ["f1"])


def test_semantic_reviewer_forces_editor_to_restore_essential_caveat(monkeypatch):
    from studio import narrative_layout as narrative

    def fit(p):
        p.analysis["slide_budget"] = {"status": "adjusted", "planned": 1}

    monkeypatch.setattr(narrative, "narrative_storyboard", fit)
    p = SimpleNamespace(
        content=source(),
        original_content=None,
        template=SimpleNamespace(patterns=[]),
        constraints=Constraints(slides=1, count_mode="exact", summarize=True),
        analysis={},
    )

    class Gateway:
        settings = SimpleNamespace()

        def __init__(self):
            self.reviews = 0
            self.edits = 0

        async def json_request(self, stage, payload, **kwargs):
            if stage == "editorial":
                self.edits += 1
                assert self.edits == 1
                incomplete = plan()
                incomplete["slides"][0]["bullets"].pop()
                return incomplete
            if stage == "editorial_repair":
                assert payload["revision_feedback"]["essential_facts_to_restore"] == ["f2"]
                self.edits += 1
                return {"replacements": [{"slide": 1, "content": plan()["slides"][0]}]}
            self.reviews += 1
            return {
                "claims": [
                    {"claim_id": c["claim_id"], "supported": True, "meaning_preserved": True}
                    for c in payload["claims"]
                ],
                "missing_essential_fact_ids": ["f2"] if self.reviews == 1 else [],
                "narrative_coherent": True,
            }

    gateway = Gateway()
    assert asyncio.run(prepare_editorial(p, gateway))
    assert gateway.edits == 2 and p.analysis["editorial"]["attempts"] == 2


def test_geometry_retry_passes_real_field_limits_and_keeps_original_source(monkeypatch):
    from studio import narrative_layout as narrative

    fits = []

    def fit(p):
        fits.append(True)
        p.analysis["slide_budget"] = (
            {
                "status": "needs_input",
                "message": "Card too small",
                "fit_issues": [text_fit_issue()],
            }
            if len(fits) == 1
            else {"status": "adjusted"}
        )

    monkeypatch.setattr(narrative, "narrative_storyboard", fit)
    p = SimpleNamespace(
        content=source(),
        original_content=None,
        template=SimpleNamespace(patterns=[]),
        constraints=Constraints(slides=1, count_mode="exact", summarize=True),
        analysis={},
    )

    class Gateway:
        settings = SimpleNamespace()
        edits = 0

        async def json_request(self, stage, payload, **kwargs):
            if stage in ("editorial", "editorial_repair"):
                self.edits += 1
                assert len(payload["source"]["facts"]) == 3
                if self.edits == 2:
                    assert (
                        payload["revision_feedback"]["fields"][0]["fields"][0][
                            "target_max_characters"
                        ]
                        == 28
                    )
                    assert payload["slide_range"] == [1, 1]
                    shorter = plan()["slides"][0]
                    shorter["bullets"][0]["text"] = "Ускорить обработку заявок."
                    return {"replacements": [{"slide": 1, "content": shorter}]}
                return plan()
            return {
                "claims": [
                    {"claim_id": c["claim_id"], "supported": True, "meaning_preserved": True}
                    for c in payload["claims"]
                ],
                "narrative_coherent": True,
            }

    assert asyncio.run(prepare_editorial(p, Gateway()))
    assert p.analysis["editorial"]["attempts"] == 2


def test_rejected_summary_is_never_committed_and_corrections_survive(monkeypatch):
    from studio import narrative_layout as narrative

    fits = []

    def fit(p):
        fits.append(True)
        p.analysis["slide_budget"] = (
            {"status": "adjusted"}
            if len(fits) == 1
            else {
                "status": "needs_input",
                "message": "Small field",
                "fit_issues": [text_fit_issue()],
            }
        )

    monkeypatch.setattr(narrative, "narrative_storyboard", fit)
    p = SimpleNamespace(
        content=source(),
        original_content=None,
        template=SimpleNamespace(patterns=[]),
        constraints=Constraints(slides=1, count_mode="exact", summarize=True),
        analysis={},
    )

    class Gateway:
        settings = SimpleNamespace()
        edits = 0

        async def json_request(self, stage, payload, **kwargs):
            if stage in ("editorial", "editorial_repair"):
                self.edits += 1
                if self.edits > 1:
                    assert payload["revision_feedback"]["fields"] == (
                        [] if self.edits == 2 else [text_fit_issue()]
                    )
                    assert (
                        payload["revision_feedback"]["previous_semantic_corrections"][0]["claims"][
                            0
                        ]["meaning_preserved"]
                        is False
                    )
                    shorter = plan()["slides"][0]
                    shorter["bullets"][0]["text"] = "Ускорить обработку заявок."
                    return {"replacements": [{"slide": 1, "content": shorter}]}
                return plan()
            return {
                "claims": [
                    {
                        "claim_id": c["claim_id"],
                        "supported": True,
                        "meaning_preserved": False,
                        "issue": "Lost qualification",
                    }
                    for c in payload["claims"]
                ],
                "narrative_coherent": True,
            }

    from studio.induction import InductionFailure

    gateway = Gateway()
    with pytest.raises(InductionFailure):
        asyncio.run(prepare_editorial(p, gateway))
    assert gateway.edits == 4  # one actual correction, then two rejected no-op responses
    assert p.analysis["editorial_repair_diagnostics"]["feedback"]["fields"] == [text_fit_issue()]
    assert len(p.content.facts) == 3 and "editorial" not in p.analysis


def test_repeated_evidenced_date_is_allowed_but_new_dates_and_labels_are_reported():
    c = parse_content("В 1938 году произошли погромы.")
    raw = {
        "slides": [
            {
                "title": "1938 год",
                "purpose": "timeline",
                "bullets": [
                    {
                        "text": "1938: погромы в 1938 году.",
                        "group": "1938",
                        "evidence": [{"fact_id": "f1", "quote": c.facts[0].text}],
                    }
                ],
            }
        ]
    }
    validate_plan(raw, c, (1, 1))
    raw["slides"][0]["bullets"][0]["group"] = "1939"
    raw["slides"][0]["title"] = "1940"
    with pytest.raises(ValueError) as error:
        validate_plan(raw, c, (1, 1))
    assert "s1b1" in str(error.value) and "1939" in str(error.value)
    assert "s1 title" in str(error.value) and "1940" in str(error.value)


def test_numbers_from_uncited_facts_do_not_support_a_claim():
    c = parse_content("Переезд в 1913 году. Война началась в 1914 году.")
    raw = {
        "slides": [
            {
                "title": "Переезд и война",
                "bullets": [
                    {
                        "text": "Переезд в 1913 году, война в 1914.",
                        "evidence": [{"fact_id": "f1", "quote": c.facts[0].text}],
                    }
                ],
            }
        ]
    }
    with pytest.raises(ValueError, match="1914"):
        validate_plan(raw, c, (1, 1))


def test_timeline_requires_grounded_time_labels_and_numbers():
    from studio.editorial_patch_validation import numeric_evidence_hints

    c = parse_content("Волк сдул дом. Волк сломал шалаш. Поросята спрятались в норе.")
    raw = {
        "slides": [
            {
                "title": "Атака на дома",
                "purpose": "timeline",
                "bullets": [
                    {
                        "group": f"Этап {i}",
                        "text": fact.text,
                        "evidence": [{"fact_id": fact.id}],
                    }
                    for i, fact in enumerate(c.facts, 1)
                ],
            }
        ]
    }
    with pytest.raises(ValueError) as error:
        validate_plan(raw, c, (1, 1))
    assert "timeline labels need dates or time" in str(error.value)
    assert "Unsupported number" in str(error.value)
    assert {row["unsupported_numbers"][0] for row in numeric_evidence_hints(raw, [1], c)} == {
        "1",
        "2",
        "3",
    }

    unnumbered = deepcopy(raw)
    for claim, group in zip(unnumbered["slides"][0]["bullets"], ["Дом", "Шалаш", "Нора"]):
        claim["group"] = group
    with pytest.raises(ValueError, match="timeline labels need dates or time"):
        validate_plan(unnumbered, c, (1, 1))

    corrected = deepcopy(unnumbered)
    corrected["slides"][0]["purpose"] = "content"
    validate_plan(corrected, c, (1, 1))

    numbered_claim = deepcopy(corrected)
    numbered_claim["slides"][0]["bullets"][0]["text"] += " За 10 секунд."
    with pytest.raises(ValueError, match="10"):
        validate_plan(numbered_claim, c, (1, 1))


def test_citation_id_is_resolved_to_literal_source_without_weakening_grounding():
    raw = plan()
    raw["slides"][0]["bullets"][0]["evidence"] = [{"fact_id": "f1"}]
    out = validate_plan(raw, source(), (1, 1))
    assert out["slides"][0]["bullets"][0]["evidence"][0]["quote"] == source().facts[0].text
    raw["slides"][0]["bullets"][0]["text"] = "Экономия 50%."
    with pytest.raises(ValueError, match="number"):
        validate_plan(raw, source(), (1, 1))
    raw = plan()
    raw["slides"][0]["bullets"][0]["evidence"] = [{"fact_id": "missing"}]
    with pytest.raises(ValueError, match="unknown source"):
        validate_plan(raw, source(), (1, 1))
