from studio.contents.semantic_bindings import (
    content_groups,
    bind_groups,
    canonicalize_storyboard,
    object_contract,
)
from studio.models import Fact, SlidePlan, Box, TableData
from studio.contents.planner import extractive_plans


def test_identical_prices_stay_with_their_entities(prepared):
    _, _, p = prepared
    p.content.facts = [
        Fact(id="a", text="Цена 100 рублей.", section="А"),
        Fact(id="b", text="Цена 100 рублей.", section="Б"),
    ]
    p.content.tables = []
    p.analysis = {
        "archetypes": {
            "units": [
                {
                    "purpose": "comparison",
                    "fact_ids": ["a", "b"],
                    "slots": [
                        {"role": "entity", "fact_id": "a", "quote": "А", "source": "section"},
                        {"role": "entity", "fact_id": "b", "quote": "Б", "source": "section"},
                        {"role": "criterion", "fact_id": "a", "quote": "Цена", "source": "text"},
                    ],
                }
            ]
        }
    }
    slide = SlidePlan(title="Тарифы", fact_ids=["a", "b"], purpose="comparison")
    result = content_groups(slide, p)
    assert result["status"] == "specialized"
    assert [(g["label"], [f.id for f in g["facts"]]) for g in result["groups"]] == [
        ("А", ["a"]),
        ("Б", ["b"]),
    ]
    pattern = p.template.patterns[0].model_copy(deep=True)
    pattern.body_zones = [Box(x=20, y=100, w=200, h=100), Box(x=250, y=100, w=200, h=100)]
    pattern.fields = [
        {"role": "body", "index": 0, "shape_id": 11},
        {"role": "body", "index": 1, "shape_id": 22},
    ]
    assert [g["shape_id"] for g in bind_groups(slide, p, pattern)["groups"]] == [11, 22]
    pattern.body_zones.pop()
    assert bind_groups(slide, p, pattern)["status"] == "general"


def test_one_fact_mentioning_two_entities_does_not_invent_ownership(prepared):
    _, _, p = prepared
    p.content.facts = [Fact(id="a", text="А стоит 10, а Б стоит 20.")]
    p.analysis = {
        "archetypes": {
            "units": [
                {
                    "purpose": "comparison",
                    "fact_ids": ["a"],
                    "slots": [
                        {"role": "entity", "fact_id": "a", "quote": "А"},
                        {"role": "entity", "fact_id": "a", "quote": "Б"},
                    ],
                }
            ]
        }
    }
    result = content_groups(SlidePlan(title="Цена", fact_ids=["a"], purpose="comparison"), p)
    assert result["status"] == "general"
    assert result["groups"][0]["facts"][0].text == "А стоит 10, а Б стоит 20."


def test_one_storyboard_does_not_allow_variant_rewrites(prepared):
    _, _, p = prepared
    plans = extractive_plans(p)
    plans.variants[1].slides[0].title = "Другой заголовок"
    canonicalize_storyboard(plans, p)
    assert len({v.slides[0].title for v in plans.variants}) == 1
    assert p.analysis["canonical_storyboard"]


def test_physical_filter_precedes_specialized_preference(prepared):
    from studio.composition.contracts import candidates

    _, _, p = prepared
    p.content.facts = [
        Fact(id="a", text="Выполнить первый шаг."),
        Fact(id="b", text="Выполнить второй шаг."),
    ]
    p.analysis = {
        "archetypes": {
            "units": [
                {
                    "purpose": "process",
                    "fact_ids": ["a", "b"],
                    "slots": [
                        {
                            "role": "step",
                            "fact_id": "a",
                            "quote": "Выполнить первый шаг.",
                            "source": "text",
                        },
                        {
                            "role": "step",
                            "fact_id": "b",
                            "quote": "Выполнить второй шаг.",
                            "source": "text",
                        },
                    ],
                }
            ]
        }
    }
    generic = p.template.patterns[0].model_copy(deep=True)
    generic.id = "physical"
    generic.role = "statement"
    generic.purpose = "content"
    generic.source_slide = 1
    generic.body_zones = [Box(x=20, y=100, w=500, h=200)]
    exact = generic.model_copy(deep=True)
    exact.id = "layout-only"
    exact.source_slide = None
    exact.body_zones = [Box(x=20, y=100, w=200, h=100), Box(x=250, y=100, w=200, h=100)]
    p.template.patterns = [generic, exact]
    slide = SlidePlan(title="Процесс", fact_ids=["a", "b"], purpose="process")
    assert [c.id for c in candidates(p, slide)] == ["layout-only"]
    assert [c.id for c in candidates(p, slide, source_slides_only=True)] == ["physical"]
    assert len(candidates(p, slide, prefer_specialized=False)) == 2


def test_table_uses_a_fitting_field_instead_of_first_field(prepared):
    _, _, p = prepared
    p.content.tables = [
        TableData(
            id="t",
            headers=["Месяц", "Закрытые обращения"],
            rows=[["Январь", "40"], ["Февраль", "55"], ["Март", "70"]],
        )
    ]
    p.content.facts = [Fact(id="f", text="Результаты пилота", source="t")]
    pattern = p.template.patterns[0].model_copy(deep=True)
    pattern.body_zones = [Box(x=20, y=100, w=140, h=180), Box(x=200, y=100, w=340, h=220)]
    pattern.fields = [
        {"role": "title", "index": 0, "shape_id": 1, "box": pattern.title_zone.model_dump()},
        {"role": "body", "index": 0, "shape_id": 2, "box": pattern.body_zones[0].model_dump()},
        {"role": "body", "index": 1, "shape_id": 3, "box": pattern.body_zones[1].model_dump()},
    ]
    slide = SlidePlan(title="Результаты", fact_ids=["f"], table_id="t", layout="table")
    contract = object_contract(slide, p, pattern)
    assert contract["visuals"][0]["box"] == pattern.body_zones[1].model_dump()
    assert contract["binding"]["fields"][0]["shape_id"] == 3
    from studio.contents.semantic_bindings import binding_report

    assert binding_report(slide, p, pattern)["fields"][0]["shape_id"] == 3


def test_table_capacity_is_checked_after_text_reservation(prepared):
    import pytest

    _, _, p = prepared
    p.content.tables = [
        TableData(id="t", headers=["А", "Б"], rows=[["1", "2"], ["3", "4"], ["5", "6"]])
    ]
    p.content.facts = [
        Fact(id="f", text="Таблица", source="t"),
        Fact(id="body", text="Комментарий к таблице."),
    ]
    pattern = p.template.patterns[0].model_copy(deep=True)
    pattern.body_zones = [Box(x=20, y=100, w=400, h=100)]
    pattern.fields = [
        {"role": "title", "index": 0, "shape_id": 1, "box": pattern.title_zone.model_dump()},
        {"role": "body", "index": 0, "shape_id": 2, "box": pattern.body_zones[0].model_dump()},
    ]
    from studio.contents.semantic_bindings import table_capacity

    assert table_capacity(p.content.tables[0], pattern.body_zones[0], p.template) == 0
    with pytest.raises(ValueError, match="Таблица не помещается"):
        object_contract(
            SlidePlan(title="Результат", fact_ids=["f", "body"], table_id="t", layout="table"),
            p,
            pattern,
        )


def test_specialized_groups_can_use_subset_of_larger_grid(prepared):
    _, _, p = prepared
    p.content.tables = []
    p.content.facts = [Fact(id="a", text="Подать заявку."), Fact(id="b", text="Проверить заявку.")]
    p.analysis = {
        "archetypes": {
            "units": [
                {
                    "purpose": "process",
                    "fact_ids": ["a", "b"],
                    "slots": [
                        {"role": "step", "fact_id": "a", "quote": "Подать заявку."},
                        {"role": "step", "fact_id": "b", "quote": "Проверить заявку."},
                    ],
                }
            ]
        }
    }
    pattern = p.template.patterns[0].model_copy(deep=True)
    pattern.body_zones = [Box(x=20 + i * 160, y=100, w=140, h=150) for i in range(3)]
    pattern.fields = [
        {"role": "title", "index": 0, "shape_id": 1, "box": pattern.title_zone.model_dump()}
    ] + [
        {"role": "body", "index": i, "shape_id": i + 2, "box": b.model_dump()}
        for i, b in enumerate(pattern.body_zones)
    ]
    contract = object_contract(
        SlidePlan(title="Процесс", fact_ids=["a", "b"], purpose="process"), p, pattern
    )
    assert contract["binding"]["status"] == "specialized"
    assert "Подать заявку." in contract["fields"]["2"]["paragraphs"]
    assert "Проверить заявку." in contract["fields"]["3"]["paragraphs"]
    assert not contract["fields"]["4"]["paragraphs"]


def test_table_rejects_layout_when_no_field_is_readable(prepared):
    _, _, p = prepared
    p.content.tables = [
        TableData(id="t", headers=["Месяц", "Закрытые обращения"], rows=[["Январь", "40"]])
    ]
    p.content.facts = [Fact(id="f", text="Результаты пилота", source="t")]
    pattern = p.template.patterns[0].model_copy(deep=True)
    pattern.body_zones = [Box(x=20, y=100, w=120, h=40)]
    pattern.fields = [
        {"role": "title", "index": 0, "shape_id": 1, "box": pattern.title_zone.model_dump()},
        {"role": "body", "index": 0, "shape_id": 2, "box": pattern.body_zones[0].model_dump()},
    ]
    import pytest

    with pytest.raises(ValueError, match="Таблица не помещается"):
        object_contract(
            SlidePlan(title="Результаты", fact_ids=["f"], table_id="t", layout="table"), p, pattern
        )


def test_metric_pattern_can_host_a_fitting_trend_table(prepared):
    from studio.composition.contracts import compatible

    _, _, p = prepared
    pattern = p.template.patterns[0].model_copy(deep=True)
    pattern.purpose = "metrics"
    pattern.role = "statement"
    pattern.reusable = True
    slide = SlidePlan(title="Динамика", fact_ids=[], table_id="t", layout="table", purpose="trend")
    assert compatible(pattern, slide)


def test_verified_title_only_divider_does_not_require_an_example_slide(prepared):
    from studio.composition.contracts import apply_meanings, compatible

    _, _, p = prepared
    pattern = p.template.patterns[0]
    pattern.source_slide = None
    pattern.body_zones = []
    pattern.source_layout = "Авторский макет 2"
    pattern.role = "divider"
    apply_meanings(
        p.template,
        {"patterns": [{"pattern_id": pattern.id, "purpose": "divider", "reusable": True}]},
    )
    assert compatible(
        pattern, SlidePlan(title="Раздел", fact_ids=[], purpose="divider", layout="divider")
    )


def test_native_composer_and_binding_report_use_the_same_table_field(prepared):
    from studio.composition.composer import compose_variant
    from studio.contents.semantic_bindings import binding_report
    from studio.models import VariantPlan

    _, _, p = prepared
    p.content.tables = [TableData(id="t", headers=["А", "Б"], rows=[["1", "2"], ["3", "4"]])]
    p.content.facts = [Fact(id="f", text="Таблица", source="t")]
    pattern = p.template.patterns[0].model_copy(deep=True)
    pattern.body_zones = [Box(x=20, y=100, w=120, h=35), Box(x=200, y=150, w=600, h=250)]
    pattern.fields = [f for f in pattern.fields if f["role"] == "title"] + [
        {"role": "body", "index": i, "shape_id": 100 + i, "box": box.model_dump()}
        for i, box in enumerate(pattern.body_zones)
    ]
    p.template.patterns = [pattern]
    slide = SlidePlan(title="Результаты", fact_ids=["f"], table_id="t", layout="table")
    scenes = compose_variant(VariantPlan(key="executive", title="Test", slides=[slide]), p)
    table = next(e for e in scenes[0].elements if e.kind == "table")
    target = pattern.body_zones[1]
    assert (table.box.x, table.box.y, table.box.w) == (target.x, target.y, target.w)
    assert 0 < table.box.h <= target.h  # Rows compact to their text within the same authored field.
    assert binding_report(slide, p, pattern)["fields"][0]["shape_id"] == 101


def test_shared_field_label_does_not_repeat_existing_subject_or_drop_evidence():
    from studio.contents.semantic_bindings import inline_group_text

    assert (
        inline_group_text("Ильф", ["Ильф превращает сложность в код."])
        == "Ильф превращает сложность в код."
    )
    assert (
        inline_group_text("Ильф", ["Пишет код.", "Проверяет результат."])
        == "Ильф. Пишет код.\nПроверяет результат."
    )
    assert inline_group_text("Ильф", ["Ильфов пример."]) == "Ильф. Ильфов пример."
    assert inline_group_text("1", ["10 задач."]) == "1. 10 задач."
    assert inline_group_text("", ["Точный исходный факт."]) == "Точный исходный факт."


def test_generic_asymmetric_panels_do_not_invent_agenda_hierarchy():
    from studio.composition.contracts import compatible
    from studio.models import Pattern

    pattern = Pattern(
        id="asymmetric",
        source_slide=0,
        source_layout="Generic",
        text_zones=[],
        role="content",
        title_zone=Box(x=20, y=20, w=900, h=60),
        body_zones=[Box(x=20, y=120, w=400, h=320), Box(x=460, y=120, w=400, h=50)],
        purpose="content",
        graphic_kind="none",
    )
    for purpose in ("agenda", "comparison", "structure", "process"):
        assert not compatible(pattern, SlidePlan(title="Темы", fact_ids=[], purpose=purpose))
    # A generic content split remains available when no peer relationship is claimed.
    assert compatible(pattern, SlidePlan(title="Обзор", fact_ids=[], purpose="content"))
    pattern.body_zones[1].h = 250
    assert compatible(pattern, SlidePlan(title="Темы", fact_ids=[], purpose="agenda"))
