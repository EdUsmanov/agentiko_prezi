from types import SimpleNamespace

from studio.composition.charts import chart_projection, make_chart
from studio.contents.planner import assign_compositions, validate_plans
from studio.models import Box, ContentModel, Fact, Plans, SlidePlan, TableData, VariantPlan


def _element(table):
    return make_chart(
        table,
        SlidePlan(title="Delivery", fact_ids=["source"], layout="chart"),
        Box(x=0, y=0, w=600, h=360),
        SimpleNamespace(font="Arial", body_size=20, accent="#123456"),
        "#000000",
        ["source"],
    )


def test_count_and_hour_headers_keep_all_source_cells_in_editable_table():
    table = TableData(
        id="pilot",
        headers=[
            "Команда / Team",
            "Заявки / Requests",
            "Выполнено / Closed",
            "Среднее время, ч / Hours",
        ],
        rows=[
            ["Север / North", "40", "34", "18"],
            ["Центр / Central", "36", "33", "15"],
            ["Юг / South", "28", "24", "21"],
        ],
    )
    projected, caption = chart_projection(table)
    assert projected.headers == table.headers[:3]
    assert all("Hours" in line for line in caption)
    element = _element(table)
    assert element.kind == "table"
    assert element.rows == [table.headers] + table.rows


def test_explicit_percent_and_currency_headers_do_not_share_count_axis():
    for header in ("Completion %", "Revenue, ₽"):
        table = TableData(
            id="units",
            headers=["Week", "Requests", header],
            rows=[["1", "40", "34"], ["2", "36", "33"]],
        )
        assert _element(table).kind == "table"


def test_rubles_and_dollars_must_not_share_one_unlabeled_axis():
    table = TableData(
        id="currencies",
        headers=["Region", "Revenue, ₽", "Revenue, $"],
        rows=[["North", "40", "34"], ["South", "36", "33"]],
    )
    assert _element(table).kind == "table"


def test_comparable_count_columns_remain_native_chart():
    table = TableData(
        id="counts",
        headers=["Team", "Requests", "Closed"],
        rows=[["North", "40", "34"], ["Central", "36", "33"]],
    )
    element = _element(table)
    assert element.kind == "chart"
    assert element.series_values == [[40, 36], [34, 33]]


def _planning_case(visualization="auto"):
    table = TableData(
        id="pilot",
        headers=["Team", "Requests", "Closed", "Hours"],
        rows=[["North", "40", "34", "18"], ["South", "28", "24", "21"]],
        visualization=visualization,
    )
    package = SimpleNamespace(
        content=ContentModel(
            title="Data", facts=[Fact(id="f1", text="Source data", source="pilot")], tables=[table]
        ),
        constraints=SimpleNamespace(slides=1),
        control=SimpleNamespace(slide_budget=None),
        analysis={},
        template=SimpleNamespace(patterns=[]),
    )
    plans = Plans(
        variants=[
            VariantPlan(
                key=key,
                title=key,
                slides=[SlidePlan(title="Data", fact_ids=["f1"], table_id="pilot", layout="chart")],
            )
            for key in ("executive", "analytical", "story")
        ]
    )
    return package, plans


def test_auto_chart_is_changed_in_plan_before_export_audit():
    package, plans = _planning_case()
    final = assign_compositions(validate_plans(plans, package), package)
    assert all(
        s.layout == "table" and s.chart_type == "auto" for v in final.variants for s in v.slides
    )


def test_editorial_chart_choice_falls_back_with_visible_warning():
    package, plans = _planning_case("column")
    package.analysis["editorial"] = {"status": "completed"}
    final = assign_compositions(validate_plans(plans, package), package)
    assert all(
        s.layout == "table" and s.chart_type == "auto" for v in final.variants for s in v.slides
    )
    assert len(package.analysis["warnings"]) == 1
    assert "Все исходные значения сохранены" in package.analysis["warnings"][0]


def test_multi_series_pie_plan_falls_back_before_chart_coverage_audit():
    package, plans = _planning_case("pie")
    package.content.tables[0].headers = ["Team", "Requests", "Closed", "Other"]
    final = assign_compositions(validate_plans(plans, package), package)
    assert all(
        s.layout == "table" and s.chart_type == "auto" for v in final.variants for s in v.slides
    )
    assert len(package.analysis["warnings"]) == 1


def test_valid_single_series_pie_remains_a_native_chart():
    table = TableData(
        id="shares",
        headers=["Team", "Requests"],
        rows=[["North", "40"], ["South", "28"]],
    )
    element = make_chart(
        table,
        SlidePlan(title="Shares", fact_ids=["source"], chart_type="pie"),
        Box(x=0, y=0, w=600, h=360),
        SimpleNamespace(font="Arial", body_size=20, accent="#123456"),
        "#000000",
        ["source"],
    )
    assert element.kind == "chart" and element.chart_type == "pie"
