"""Regression: direct table references and ineffective cached editorial patches."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
import pytest
from studio.content import parse_content
from studio.editorial import validate_plan, apply_plan, EditorialPlan
from studio.editorial_repair import prepare_with_targeted_repairs, plan_signature
from studio.models import Constraints


def table_case():
    source = parse_content(
        "Результат пилота.\n\n| Спринт | Задачи |\n|---|---|\n| 1 | 18 |\n| 2 | 24 |"
    )
    prose = next(f for f in source.facts if f.source == "user_text")
    table = source.tables[0]
    raw = {
        "slides": [
            {
                "title": "Результат пилота",
                "purpose": "trend",
                "bullets": [{"text": prose.text, "evidence": [{"fact_id": prose.id}]}],
                "source_table_id": table.id,
                "relationship": "comparison",
                "chart_type": "column",
            }
        ]
    }
    return source, table, raw


def test_source_table_is_grounded_without_redundant_bullet_citation():
    source, table, raw = table_case()
    accepted = validate_plan(raw, source, (1, 1))
    owner = next(f.id for f in source.facts if f.source == table.id)
    assert owner not in [o["fact_id"] for o in accepted["omitted"]]
    # The claim retains its actual evidence, never receives unrelated table evidence.
    assert owner not in [e["fact_id"] for e in accepted["slides"][0]["bullets"][0]["evidence"]]
    package = SimpleNamespace(content=source, original_content=source, analysis={})
    apply_plan(package, accepted, {}, (1, 1))
    assert package.content.tables[0].rows == table.rows


def test_table_reference_does_not_ground_an_uncited_numeric_claim():
    source, table, raw = table_case()
    raw["slides"][0]["bullets"][0]["text"] = "Закрыто 24 задачи."
    with pytest.raises(ValueError, match="Unsupported number"):
        validate_plan(raw, source, (1, 1))
    raw["slides"][0]["source_table_id"] = "unknown"
    with pytest.raises(ValueError, match="Unknown source_table_id"):
        validate_plan(raw, source, (1, 1))


def test_patch_signature_ignores_only_resolved_quotes():
    _, _, raw = table_case()
    raw = EditorialPlan.model_validate(raw).model_dump()
    other = deepcopy(raw)
    other["slides"][0]["bullets"][0]["evidence"][0]["quote"] = "resolved source"
    assert plan_signature(raw) == plan_signature(other)
    other["slides"][0]["bullets"][0]["text"] = "Другое утверждение."
    assert plan_signature(raw) != plan_signature(other)


def test_unchanged_patch_is_rejected_then_corrected_with_feedback(monkeypatch, tmp_path):
    from studio import narrative_layout as narrative

    monkeypatch.setattr(
        narrative,
        "narrative_storyboard",
        lambda p: p.analysis.update(slide_budget={"status": "adjusted"}),
    )
    source = parse_content("Пилот ускоряет обработку. Экономия не гарантирована.")
    initial = {
        "slides": [
            {
                "title": "Пилот",
                "purpose": "content",
                "bullets": [{"text": "Пилот ускоряет обработку.", "evidence": [{"fact_id": "f1"}]}],
            }
        ]
    }
    package = SimpleNamespace(
        content=source,
        original_content=None,
        template=SimpleNamespace(patterns=[]),
        constraints=Constraints(slides=1, count_mode="exact", summarize=True),
        analysis={},
    )

    class Gateway:
        settings = SimpleNamespace(data_dir=tmp_path)
        calls = []
        repairs = 0

        async def json_request(self, stage, payload, **kwargs):
            if stage == "editorial_repair":
                self.repairs += 1
                slide = deepcopy(payload["previous_plan"]["slides"][0])
                if self.repairs == 2:
                    assert "already rejected plan" in payload["validation_feedback"]
                    slide["bullets"][0]["text"] = "Обработка ускоряется в пилоте."
                return {"replacements": [{"slide": 1, "content": slide}]}
            return {
                "claims": [
                    {"claim_id": c["claim_id"], "supported": True, "meaning_preserved": True}
                    for c in payload["claims"]
                ],
                "narrative_coherent": True,
            }

    gateway = Gateway()
    assert asyncio.run(
        prepare_with_targeted_repairs(
            package,
            gateway,
            starting_plan=initial,
            quality_feedback=[{"slide": 1, "message": "Переформулировать тезис"}],
        )
    )
    assert gateway.repairs == 2
    assert len(package.analysis["editorial"]["repair_history"]) == 1


def test_cached_patch_is_revalidated_against_current_rejected_state(tmp_path):
    from studio.induction import validated_request
    from studio.editorial_repair import apply_replacements, EditorialPatch

    _, _, raw = table_case()
    raw = EditorialPlan.model_validate(raw).model_dump()
    first = deepcopy(raw["slides"][0])
    first["title"] = "Исправленная структура"
    second = deepcopy(first)
    second["title"] = "Уточнённая структура"

    class Gateway:
        settings = SimpleNamespace(data_dir=tmp_path)
        calls = []
        requests = 0

        async def json_request(self, *args, **kwargs):
            self.requests += 1
            return {
                "replacements": [{"slide": 1, "content": first if self.requests == 1 else second}]
            }

    g = Gateway()
    seen = {plan_signature(raw)}

    def validate(value):
        changed = apply_replacements(raw, value, [1])
        if plan_signature(changed) in seen:
            raise ValueError("Repeated rejected plan")
        return value

    async def check():
        args = (
            g,
            "editorial_repair",
            {"same": "payload"},
            EditorialPatch.model_json_schema(),
            validate,
        )
        patch = await validated_request(*args, timeout=1)
        seen.add(plan_signature(apply_replacements(raw, patch, [1])))
        replacement = await validated_request(*args, timeout=1)
        assert replacement["replacements"][0]["content"]["title"] == second["title"]

    asyncio.run(check())
    assert g.requests == 2  # same cache key, but rejected cached patch cannot advance the loop


def test_fixing_a_forged_quote_counts_as_a_real_repair():
    source, _, raw = table_case()
    raw = EditorialPlan.model_validate(raw).model_dump()
    bad = deepcopy(raw)
    bad["slides"][0]["bullets"][0]["evidence"][0]["quote"] = "not a source quote"
    assert plan_signature(bad, source) != plan_signature(raw, source)
    restored = deepcopy(raw)
    restored["slides"][0]["bullets"][0]["evidence"][0]["quote"] = source.facts[0].text
    assert plan_signature(restored, source) == plan_signature(raw, source)


def test_displayed_table_cannot_remain_in_omissions_after_targeted_patch():
    from studio.editorial_repair import apply_replacements
    from studio.editorial import review_payload

    source, table, raw = table_case()
    owner = next(f.id for f in source.facts if f.source == table.id)
    raw["omitted"] = [
        {"fact_id": owner, "reason": "duplicate", "explanation": "Already displayed in the table."}
    ]
    raw = EditorialPlan.model_validate(raw).model_dump()
    snapshot = deepcopy(raw)
    replacement = deepcopy(raw["slides"][0])
    replacement["title"] = "Результаты команды"
    patched = apply_replacements(raw, {"replacements": [{"slide": 1, "content": replacement}]}, [1])
    accepted = validate_plan(patched, source, (1, 1))
    assert raw == snapshot and patched["omitted"] == snapshot["omitted"]
    assert accepted["omitted"] == []
    review = review_payload(accepted, source)
    assert review["slides"][0]["source_table_id"] == table.id and review["omitted"] == []
    p = SimpleNamespace(content=source, original_content=source, analysis={})
    apply_plan(p, accepted, {}, (1, 1))
    assert p.content.tables[0].rows == table.rows


def test_undisplayed_table_omission_is_preserved_for_semantic_review():
    source, table, raw = table_case()
    raw["slides"][0].update(source_table_id=None, chart_type="auto")
    owner = next(f.id for f in source.facts if f.source == table.id)
    raw["omitted"] = [{"fact_id": owner, "reason": "detail", "explanation": "Secondary data"}]
    accepted = validate_plan(raw, source, (1, 1))
    assert accepted["omitted"] == raw["omitted"]


@pytest.mark.parametrize("invalid", ["unknown", "duplicate", "uncited_number", "bad_columns"])
def test_omission_reconciliation_does_not_hide_invalid_evidence(invalid):
    source, table, raw = table_case()
    owner = next(f.id for f in source.facts if f.source == table.id)
    raw["omitted"] = [{"fact_id": owner, "reason": "duplicate", "explanation": "Already displayed"}]
    if invalid == "unknown":
        raw["omitted"][0]["fact_id"] = "f-does-not-exist"
    elif invalid == "duplicate":
        raw["omitted"] *= 2
    elif invalid == "uncited_number":
        raw["slides"][0]["bullets"][0]["text"] = "Закрыто 24 задачи."
    else:
        raw["slides"][0]["source_columns"] = [0, 9]
    with pytest.raises(ValueError):
        validate_plan(raw, source, (1, 1))
