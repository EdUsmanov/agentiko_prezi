"""Typography normalization must not weaken numeric or evidence contracts."""

import asyncio
from types import SimpleNamespace

import pytest

from studio.content import parse_content
from studio.editorial_domain import nums, validate_plan
from studio.models import Constraints, Plans, SlidePlan, VariantPlan
from studio.narrative import numbers, validate_narrative
from studio.numeric_text import normalize_numeric_typography
from studio.planner import NAMES, validate_plans
from studio.sections import prepare_sections


@pytest.mark.parametrize("text", ["1 234", "1\u00a0234", "1\u202f234", "1\u2009234", "1234"])
def test_thousands_typography_agrees_across_numeric_checks(text):
    assert nums(text) == nums("1234")
    assert numbers(text) == numbers("1234")
    assert normalize_numeric_typography(text) == "1234"


@pytest.mark.parametrize("text", ["1 2", "2026 123", "12 34", "1 2345", "28.09.2026", "1–3"])
def test_adjacent_numbers_dates_and_ranges_are_not_joined(text):
    assert normalize_numeric_typography(text) == text


def test_decimal_percentage_spacing_is_equivalent_but_sign_and_unit_are_not():
    assert numbers("Доля: 12,5 %") == numbers("12.5%")
    assert nums("−1\u202f234,5") == nums("-1234.5")
    assert nums("-1234") != nums("1234")
    assert numbers("12.5%") != numbers("12.5")
    assert nums("1–3") == nums("1 3")
    assert nums("1−3") != nums("1 3")


def test_numbers_from_separate_facts_cannot_merge_into_a_thousands_group():
    from studio.models import ContentModel, Fact

    source = ContentModel(
        title="Evidence",
        facts=[Fact(id="f1", text="Количество 1"), Fact(id="f2", text="234 заявки отдельно")],
    )
    raw = editorial("Получено 1234 заявки.")
    raw["slides"][0]["bullets"][0]["evidence"].append({"fact_id": "f2"})
    with pytest.raises(ValueError, match="Unsupported number"):
        validate_plan(raw, source, (1, 1))
    narrative = {
        "slides": [
            {
                "title": "Получено 1234 заявки",
                "excerpts": [{"fact_id": f.id, "quotes": [f.text]} for f in source.facts],
            }
        ]
    }
    with pytest.raises(ValueError, match="Unsupported title number"):
        validate_narrative(narrative, source)
    package = SimpleNamespace(
        content=source,
        analysis={},
        constraints=Constraints(slides=1),
        template=SimpleNamespace(patterns=[]),
    )
    plans = Plans(
        variants=[
            VariantPlan(
                key=k,
                title=k,
                slides=[
                    SlidePlan(title="Получено 1234 заявки", fact_ids=["f1", "f2"], layout="columns")
                ],
            )
            for k in NAMES
        ]
    )
    with pytest.raises(ValueError, match="Неподтверждённое число"):
        validate_plans(plans, package)


def editorial(text):
    return {
        "slides": [
            {
                "title": "Результат",
                "purpose": "content",
                "bullets": [{"text": text, "evidence": [{"fact_id": "f1"}]}],
            }
        ]
    }


def test_editorial_accepts_formatting_but_keeps_source_quote_and_rejects_changed_quantity():
    source = parse_content("Обработано 1\u00a0234 заявки.")
    before = source.model_dump()
    accepted = validate_plan(editorial("Обработано 1234 заявки."), source, (1, 1))
    assert accepted["slides"][0]["bullets"][0]["evidence"][0]["quote"] == source.facts[0].text
    assert source.model_dump() == before
    for text in ["Обработано 1235 заявок.", "Обработано -1234 заявки."]:
        with pytest.raises(ValueError, match="Unsupported number"):
            validate_plan(editorial(text), source, (1, 1))


def test_narrative_title_formatting_never_allows_shortened_or_forged_source_quote():
    source = parse_content("Обработано 1\u00a0234 заявки.")
    raw = {
        "slides": [
            {
                "title": "Обработано 1234 заявки",
                "excerpts": [{"fact_id": "f1", "quotes": [source.facts[0].text]}],
            }
        ]
    }
    assert validate_narrative(raw, source)
    raw["slides"][0]["excerpts"][0]["quotes"] = ["Обработано 1234 заявки."]
    with pytest.raises(ValueError, match="verbatim"):
        validate_narrative(raw, source)


@pytest.mark.parametrize(
    "title,valid",
    [
        ("Обработано 1234 заявки", True),
        ("Обработано 1235 заявок", False),
        ("Изменение -1234", False),
    ],
)
def test_generation_plan_uses_same_numeric_evidence(title, valid):
    source = parse_content("Обработано 1\u00a0234 заявки.")
    package = SimpleNamespace(
        content=source,
        analysis={},
        constraints=Constraints(slides=1),
        template=SimpleNamespace(patterns=[]),
    )
    plans = Plans(
        variants=[
            VariantPlan(
                key=k,
                title=k,
                slides=[SlidePlan(title=title, fact_ids=["f1"], layout="columns")],
            )
            for k in NAMES
        ]
    )
    if valid:
        assert validate_plans(plans, package)
    else:
        with pytest.raises(ValueError, match="Неподтверждённое число"):
            validate_plans(plans, package)


@pytest.mark.parametrize(
    "quantity,status", [("1234", "completed"), ("1235", "failed"), ("-1234", "failed")]
)
def test_chapter_titles_keep_numeric_evidence_rules(quantity, status):
    source = parse_content(
        "# Материалы\n## A\nОбработано 1\u00a0234 заявки.\n## B\nПроверка завершена."
        "\n## C\nРезультат согласован.\n## D\nВыводы сохранены."
    )
    package = SimpleNamespace(content=source, analysis={}, constraints=Constraints(slides=10))

    class Gateway:
        settings = SimpleNamespace(mode="api")

        async def json_request(self, *args, **kwargs):
            return {
                "chapters": [
                    {"title": f"Обработано {quantity} заявки", "sections": [0, 1]},
                    {"title": "Результаты", "sections": [2, 3]},
                ]
            }

    asyncio.run(prepare_sections(package, Gateway()))
    assert package.analysis["section_grouping"]["status"] == status


@pytest.mark.parametrize("separator", ["-", "–", "—"])
def test_three_digit_range_endpoints_are_not_thousands(separator):
    assert nums(f"100{separator}200") == {"100": 1, "200": 1}
    assert nums(f"1 000{separator}2 000") == {"1000": 1, "2000": 1}
