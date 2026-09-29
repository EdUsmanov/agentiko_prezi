"""Repair routes must follow typed evidence, not messages or familiar templates."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from studio.contents.parsing import parse_content
from studio.contents.editorial import EditorialPlan, validate_plan
from studio.contents.editorial_patch_validation import (
    shortening_contracts,
    validate_contracts,
    validate_repaired_plan,
)
from studio.contents.editorial_repair import prepare_with_targeted_repairs, validation_targets
from studio.models import (
    Box,
    Constraints,
    Element,
    Finding,
    PreparationControl,
    SlideBudget,
    SlideScene,
)
from studio.checks.repair_errors import (
    LayoutCapacityError,
    PlanValidationError,
    RepairIssue,
    validation_issues,
)
from studio.checks.repair_policy import scene_fit_feedback


def claim(text, fact="f1"):
    return {
        "title": "Topic",
        "purpose": "content",
        "bullets": [{"text": text, "evidence": [{"fact_id": fact}]}],
    }


def test_duplicate_addresses_only_offender_not_original_or_quoted_identifiers():
    source = parse_content("Label s7b2 is a source label.")
    raw = {"slides": [claim(source.facts[0].text), claim(source.facts[0].text)]}
    with pytest.raises(PlanValidationError) as caught:
        validate_plan(raw, source, (2, 2))
    error = caught.value
    assert validation_targets(error, 10) == [2]
    assert error.issues[0].code == "duplicate_claim" and error.issues[0].claim == 1
    assert "s1b1" in str(error) and "s7b2" in str(error)
    translated = PlanValidationError(
        [
            issue.model_copy(update={"message": "Совершенно другая формулировка"})
            for issue in error.issues
        ]
    )
    assert validation_targets(translated, 10) == [2]
    assert validation_targets(ValueError("s2: misleading source text"), 10) == []


def test_schema_errors_keep_slide_and_claim_without_parsing_messages():
    with pytest.raises(ValueError) as caught:
        validate_plan({"slides": [claim("ok"), claim("")]}, parse_content("Source."), (2, 2))
    issues = validation_issues(caught.value)
    assert issues[0].slide == 2 and issues[0].claim == 1
    assert issues[0].code == "schema.string_too_short"
    assert validation_targets(caught.value, 2) == [2]


def test_patch_filter_does_not_edit_neighbor_named_in_duplicate_message():
    source = parse_content("Original statement. Other statement.")
    raw = EditorialPlan.model_validate(
        {"slides": [claim("Original statement."), claim("Original statement.")]}
    ).model_dump()
    # The error is on slide 2, whose diagnostic mentions the valid original s1b1.
    validate_repaired_plan(raw, source, (2, 2), 500, False, [1])
    with pytest.raises(PlanValidationError) as caught:
        validate_repaired_plan(raw, source, (2, 2), 500, False, [2])
    assert validation_targets(caught.value, 2) == [2]


def scene(kind="table"):
    return SlideScene(
        title="Capacity",
        background="#FFFFFF",
        source_ids=["f1"],
        layout="table",
        elements=[
            Element(
                kind="text",
                box=Box(x=10, y=10, w=200, h=60),
                text="Fits already",
                source_ids=["summary-3-1"],
            ),
            Element(
                kind=kind,
                box=Box(x=10, y=100, w=200, h=30),
                text="Too much text",
                size=12,
                rows=[["Header"], ["Original data"]],
                source_ids=["summary-3-2"],
            ),
        ],
    )


@pytest.mark.parametrize(
    "kind,code,action",
    [
        ("table", "table_overflow", "adapt_layout"),
        ("table", "readability", "adapt_layout"),
        ("chart", "chart_overflow", "adapt_layout"),
        ("chart", "readability", "adapt_layout"),
        ("text", "text_overflow", "shorten_text"),
        ("text", "readability", "shorten_text"),
        ("text", "overlap", "adapt_layout"),
        ("text", "out_of_bounds", "adapt_layout"),
        ("text", "container_overflow", "adapt_layout"),
    ],
)
def test_geometry_route_uses_object_kind_and_real_slide_number(kind, code, action):
    original = scene(kind)
    finding = Finding(
        code=code, message="s99 misleading wording", slide=1, element=1, severity="error"
    )
    feedback = scene_fit_feedback(
        original, [finding], 3, editable_fact_ids={"summary-3-1", "summary-3-2"}
    )
    assert feedback["repair_issues"][0]["action"] == action
    assert feedback["repair_issues"][0]["slide"] == 3
    assert feedback["findings"][0]["slide"] == 3
    assert [field["element"] for field in feedback["fields"]] == (
        [1] if action == "shorten_text" else []
    )
    assert original.elements[0].text == "Fits already"


def test_unknown_element_is_not_assumed_to_be_prose():
    issue = Finding(code="readability", message="Too small", severity="warning")
    feedback = scene_fit_feedback(
        scene(), [issue], 2, editable_fact_ids={"summary-3-1", "summary-3-2"}
    )
    assert feedback["repair_issues"][0]["action"] == "adapt_layout"
    assert feedback["fields"] == []


def test_geometry_contract_preserves_fitting_neighbor_title_and_data():
    previous = EditorialPlan.model_validate(
        {
            "slides": [
                claim("Fits"),
                claim("Fits too"),
                {
                    "title": "Capacity",
                    "bullets": [
                        {"text": "Fits already", "evidence": [{"fact_id": "f1"}]},
                        {"text": "Too much text", "evidence": [{"fact_id": "f2"}]},
                    ],
                },
            ]
        }
    ).model_dump()
    row = scene_fit_feedback(
        scene("text"),
        [Finding(code="text_overflow", severity="error", message="overflow", element=1)],
        3,
        editable_fact_ids={"summary-3-1", "summary-3-2"},
    )
    feedback = {"repair_issues": row["repair_issues"], "fields": [row]}
    contracts = shortening_contracts(previous, [3], feedback, 500)
    changed = deepcopy(previous)
    changed["slides"][2]["bullets"][1]["text"] = "Short"
    validate_contracts(changed, contracts)
    changed["slides"][2]["bullets"][0]["text"] = "Unwanted rewrite"
    with pytest.raises(ValueError, match="unaffected bullet"):
        validate_contracts(changed, contracts)
    changed = deepcopy(previous)
    changed["slides"][2]["title"] = "Unwanted title"
    with pytest.raises(ValueError, match="unaffected title"):
        validate_contracts(changed, contracts)
    assert shortening_contracts(previous, [3], {"geometry": "shorten everything"}, 500) == []


@pytest.mark.parametrize("mixed", [False, True])
def test_unresolved_table_failure_never_calls_editorial_model(monkeypatch, mixed):
    from studio.contents import narrative_layout as narrative

    source = parse_content("Original evidence.")
    package = SimpleNamespace(
        content=source,
        original_content=None,
        template=SimpleNamespace(patterns=[]),
        constraints=Constraints(slides=1, count_mode="exact", include_cover=False, summarize=True),
        analysis={},
        control=PreparationControl(),
    )
    findings = [
        Finding(code="table_overflow", severity="error", message="Data cells need space", element=1)
    ]
    if mixed:
        findings.append(
            Finding(code="text_overflow", severity="error", message="Prose also long", element=0)
        )
    row = scene_fit_feedback(scene(), findings, 1, editable_fact_ids={"summary-3-1", "summary-3-2"})
    monkeypatch.setattr(
        narrative,
        "narrative_storyboard",
        lambda p: setattr(
            p.control, "slide_budget", SlideBudget(status="needs_input", fit_issues=[row])
        ),
    )

    class Gateway:
        settings = SimpleNamespace()

        async def json_request(self, *args, **kwargs):
            pytest.fail("Layout failure must not consume a model request")

    with pytest.raises(LayoutCapacityError) as caught:
        asyncio.run(
            prepare_with_targeted_repairs(
                package, Gateway(), starting_plan={"slides": [claim("Original evidence.")]}
            )
        )
    assert caught.value.issues[0].code == "table_overflow"
    assert (
        package.analysis["editorial_repair_diagnostics"]["stopped_reason"]
        == "layout_capacity_exhausted"
    )
    assert package.content == source and "editorial" not in package.analysis


def test_real_small_template_stops_with_data_diagnostic_without_rewriting(template, tmp_path):
    from studio.templates.parsing import analyze_template

    profile = analyze_template(template, tmp_path / "profile")
    profile.width = 400
    profile.height = 240
    profile.margin = 38
    profile.patterns = []
    profile.assets = []
    source = parse_content(
        "Delivery is recorded.\n\n| Period | Planned | Delivered |\n|---|---|---|\n"
        + "\n".join(f"| Long period label {i} | {30 + i} | {20 + i} |" for i in range(10))
    )
    table = source.tables[0]
    package = SimpleNamespace(
        template=profile,
        content=source,
        original_content=None,
        images=[],
        constraints=Constraints(slides=1, count_mode="exact", include_cover=False, summarize=True),
        analysis={},
        control=PreparationControl(),
    )
    raw = {
        "slides": [
            dict(claim("Delivery is recorded."), source_table_id=table.id, chart_type="table")
        ]
    }

    class Gateway:
        settings = SimpleNamespace()

        async def json_request(self, *args, **kwargs):
            pytest.fail("Exhausted layout must not request prose repair")

    with pytest.raises(LayoutCapacityError) as caught:
        asyncio.run(prepare_with_targeted_repairs(package, Gateway(), starting_plan=raw))
    assert any(
        issue.code in ("table_overflow", "readability") and issue.action == "adapt_layout"
        for issue in caught.value.issues
    )
    assert package.content.tables[0].rows == table.rows


def test_repair_issue_is_recorded_in_redacted_job_log(tmp_path):
    from studio.diagnostics import scope, exception
    from studio.jobs.store import Store

    store = Store(tmp_path)
    job = store.create("preparation", {})
    error = LayoutCapacityError(
        [
            RepairIssue(
                code="table_overflow",
                action="adapt_layout",
                slide=2,
                element=1,
                message="Too small",
            )
        ]
    )
    with scope(store, job["id"]):
        exception("analysis.failed", error)
    # Store log API is the same source used by the frontend log panel.
    assert store.events(job["id"])[-1]["data"]["repair_issues"][0]["code"] == "table_overflow"


@pytest.mark.parametrize(
    "explicit,purpose,expected_calls",
    [(False, "content", 2), (True, "content", 1), (False, "cover", 1), (False, "divider", 1)],
)
def test_layout_alternative_is_bounded_and_respects_explicit_choice(
    prepared, monkeypatch, explicit, purpose, expected_calls
):
    from studio.composition import composer
    from studio.models import VariantPlan, SlidePlan

    _, _, package = prepared
    font = package.template.font
    original = SlideScene(
        title="Topic",
        background=package.template.background,
        source_ids=[package.content.facts[0].id],
        layout="columns",
        purpose=purpose,
        pattern_id="authored",
        elements=[
            Element(
                kind="text",
                box=Box(x=40, y=100, w=200, h=2),
                text="Readable original content",
                font=font,
                size=18,
                color=package.template.foreground,
                source_ids=[package.content.facts[0].id],
            )
        ],
    )
    alternative = original.model_copy(deep=True)
    alternative.pattern_id = None
    alternative.elements[0].box.h = 100
    calls = []

    def compose(variant, *args):
        calls.append(variant.slides[0].pattern_id)
        return (
            alternative if variant.slides[0].pattern_id == "token:auto" else original
        ).model_copy(deep=True)

    monkeypatch.setattr(composer, "_compose_slide", compose)
    variant = VariantPlan(
        key="executive",
        title="Test",
        slides=[
            SlidePlan(
                title="Topic",
                fact_ids=original.source_ids,
                layout="columns",
                purpose=purpose,
                pattern_id="authored" if explicit else None,
            )
        ],
    )
    result = composer.compose_slide(variant, package, 0, [])
    if expected_calls == 2:
        from studio.composition.contracts import candidates

        masters = [
            p.id
            for p in candidates(package, variant.slides[0], prefer_specialized=False)
            if not p.source_slide
        ]
        # Try each compatible master once before the generic alternative.
        assert calls == [None, *masters, "token:auto"]
        assert result.pattern_id is None and result.elements[0].text == original.elements[0].text
        assert variant.slides[0].pattern_id is None
    else:
        assert len(calls) == expected_calls
        assert result.pattern_id == "authored"


def test_analysis_failure_report_keeps_machine_readable_reason(prepared, monkeypatch):
    import json
    from dataclasses import replace
    from studio import pipeline
    from studio.preparation.contracts import TemplateAnalysisResult

    settings, store, package = prepared

    class Gateway:
        calls = []
        induction_errors = []

        def __init__(self, *args):
            pass

    async def fail(*args, **kwargs):
        raise LayoutCapacityError(
            [
                RepairIssue(
                    code="table_overflow",
                    action="adapt_layout",
                    slide=2,
                    element=1,
                    message="Data do not fit",
                )
            ]
        )

    async def template_result(*args):
        return TemplateAnalysisResult(
            package.template, package.analysis, package.manifest["template_layers"]
        )

    monkeypatch.setattr(pipeline, "ModelGateway", Gateway)
    monkeypatch.setattr("studio.preparation.orchestrator.prepare_template_result", template_result)
    monkeypatch.setattr(pipeline, "prepare_intelligence", fail)
    pipeline.prepare(
        store, package.id, "One fact. Another fact.", "", "", 2, replace(settings, mode="api")
    )
    job = store.get(package.id)
    assert job["state"] == "failed"
    report = json.loads((store.directory(package.id) / "failure-report.json").read_text())
    assert report["repair_issues"][0]["code"] == "table_overflow"
    assert report["repair_issues"][0]["slide"] == 2
    assert report["repair_issues"] == job["failure_diagnostics"]["repair_issues"]


@pytest.mark.parametrize("source_ids", [["table-3"], ["summary-3-2", "table-3"], []])
def test_data_rendered_as_text_never_becomes_an_editorial_shortening(source_ids):
    original = scene("text")
    original.elements[1].source_ids = source_ids
    original.elements[1].role = "metric_value"
    feedback = scene_fit_feedback(
        original,
        [Finding(code="text_overflow", severity="error", message="Clipped value", element=1)],
        3,
        editable_fact_ids={"summary-3-1", "summary-3-2"},
    )
    assert feedback["repair_issues"][0]["action"] == "adapt_layout"
    assert feedback["fields"] == []
