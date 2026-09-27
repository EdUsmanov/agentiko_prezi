import pytest
from studio.models import Box, Fact, TableData, SlidePlan
from studio.semantic_bindings import object_contract, table_region
from studio.composer import compose
from studio.render import chart_fits


def chart_case(prepared):
    _, _, package = prepared
    package.content.facts = [
        Fact(id="f1", text="Заявки выросли.", source="user_text"),
        Fact(id="f2", text="Данные", source="t1"),
    ]
    table = TableData(
        id="t1",
        headers=["Месяц", "Заявки"],
        rows=[["Апрель", "40"], ["Май", "55"], ["Июнь", "70"]],
        visualization="column",
    )
    package.content.tables = [table]
    pattern = next(p for p in package.template.patterns if p.source_slide and p.body_zones)
    pattern = pattern.model_copy(deep=True)
    pattern.purpose = "content"
    pattern.role = "statement"
    pattern.body_zones = [Box(x=40, y=140, w=280, h=270)]
    field = next(f for f in pattern.fields if f["role"] == "body")
    field["box"] = pattern.body_zones[0].model_dump()
    package.template.patterns = [pattern]
    slide = SlidePlan(
        title="Динамика",
        fact_ids=["f1", "f2"],
        table_id="t1",
        layout="chart",
        chart_type="column",
        purpose="content",
        pattern_id=pattern.id,
    )
    return package, table, pattern, slide


def test_chart_and_body_share_an_authored_field_without_fixed_ratio_failure(prepared):
    package, _, pattern, slide = chart_case(prepared)
    scene = compose(slide, package, 1, "executive")
    chart = next(e for e in scene.elements if e.kind == "chart")
    assert chart_fits(chart, package.template)
    assert chart.box.h >= 160
    body = next(e for e in scene.elements if e.kind == "text" and e.role == "body")
    assert body.box.y >= chart.box.y + chart.box.h + 12
    contract = object_contract(slide, package, pattern)
    assert contract["visuals"][0]["box"] == chart.box.model_dump()
    assert all(
        f["box"]["y"] >= chart.box.y + chart.box.h + 12
        for f in contract["fields"].values()
        if f["role"] == "body" and f["paragraphs"]
    )


def test_narrow_table_field_is_not_accepted_for_a_requested_chart(prepared):
    package, table, pattern, _ = chart_case(prepared)
    pattern.body_zones[0].w = 220
    assert table_region(table, pattern, package.template, True)
    with pytest.raises(ValueError):
        table_region(table, pattern, package.template, True, chart=True)


def test_chart_uses_explicit_illustration_container_and_keeps_body_field(prepared):
    package, _, pattern, slide = chart_case(prepared)
    illustration = Box(x=370, y=140, w=470, h=298)
    pattern.image_zones = [illustration]
    pattern.fields.append(
        {
            "role": "image",
            "index": 0,
            "shape_id": 900,
            "box": illustration.model_dump(),
            "evidence_placeholder": True,
        }
    )
    scene = compose(slide, package, 1, "executive")
    chart = next(e for e in scene.elements if e.kind == "chart")
    assert chart.box == illustration
    body = next(e for e in scene.elements if e.kind == "text" and e.role == "body")
    assert body.box.x == pattern.body_zones[0].x
    contract = object_contract(slide, package, pattern)
    assert contract["visuals"][0]["shape_id"] == 900
    assert contract["visuals"][0]["box"] == illustration.model_dump()
    from types import SimpleNamespace
    from studio.native_template import intersects

    with_image = object_contract(slide, package, pattern, [SimpleNamespace(id="photo")])
    boxes = [Box.model_validate(v["box"]) for v in with_image["visuals"]]
    assert not intersects(boxes[0], boxes[1])
    # Removing explicit evidence permission must keep this area off limits.
    pattern.fields[-1].pop("evidence_placeholder")
    assert (
        table_region(package.content.tables[0], pattern, package.template, True, chart=True)[2]
        != illustration
    )


def test_table_with_short_caption_uses_available_height_without_tiny_type(prepared):
    from studio.audit import repair_scenes, audit_scenes

    package, _, pattern, slide = chart_case(prepared)
    pattern.body_zones = [Box(x=40, y=140, w=850, h=380)]
    field = next(f for f in pattern.fields if f["role"] == "body")
    field["box"] = pattern.body_zones[0].model_dump()
    table = TableData(
        id="t1",
        headers=["Участник", "Результат за 12 недель", "Значение"],
        rows=[
            ["Ильф", "Задачи разработки", "82"],
            ["Вадим", "Задачи разработки", "78"],
            ["Тимофей", "Проверенные тестовые сценарии", "420"],
            ["Эруард", "Согласованные критерии приёмки", "240"],
        ],
    )
    package.content.tables = [table]
    slide.layout = "table"
    slide.chart_type = "auto"
    package.content.facts[0].text = "Показатели разных типов."
    scene = compose(slide, package, 1, "executive")
    repair_scenes([scene], package)
    actual = next(e for e in scene.elements if e.kind == "table")
    assert actual.size >= 16 and actual.rows == [table.headers] + table.rows
    assert not [
        f
        for f in audit_scenes([scene], package)
        if f.code in ("table_overflow", "overlap", "container_overflow", "readability")
    ]
    body = next(e for e in scene.elements if e.role == "body" and e.kind == "text")
    assert actual.box.y + actual.box.h <= body.box.y
    assert body.box.y + body.box.h <= pattern.body_zones[0].y + pattern.body_zones[0].h
    # Final generation reporting must use the same measured region as composition.
    from studio.semantic_bindings import binding_report

    report = binding_report(slide, package, pattern)
    assert report["fields"][0]["table_id"] == table.id
    assert (
        report["fields"][0]["shape_id"]
        == object_contract(slide, package, pattern)["visuals"][0]["shape_id"]
    )
