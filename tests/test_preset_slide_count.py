"""UI size ranges must retain their meaning at the publication gate."""

import pytest
from types import SimpleNamespace

from studio.checks.audit import audit_scenes
from studio.composition.composer import compose_variant
from studio.contents.parsing import parse_content, parse_constraints
from studio.contents.planner import extractive_plans
from studio.contents.slide_budget import count_was_adjusted
from studio.models import PreparationControl, SlideBudget


@pytest.mark.parametrize(
    "slides,preset,planned,changed",
    [(5, None, 5, False), (5, None, 6, True), (None, "mini", 3, False), (None, "mini", 6, True)],
)
def test_semantic_plan_only_warns_when_count_really_changed(slides, preset, planned, changed):
    package = SimpleNamespace(
        constraints=parse_constraints(slides, "", "", preset),
        control=PreparationControl(slide_budget=SlideBudget(status="adjusted", planned=planned)),
    )
    assert count_was_adjusted(package) is changed


@pytest.mark.parametrize(
    "preset,count,blocked",
    [
        ("mini", 2, True),
        ("mini", 3, False),
        ("mini", 5, False),
        ("mini", 6, True),
        ("standard", 5, True),
        ("standard", 6, False),
        ("standard", 10, False),
        ("standard", 11, True),
    ],
)
def test_default_size_preset_accepts_its_range(prepared, preset, count, blocked):
    _, _, package = prepared
    package.analysis = {}
    package.control.slide_budget = None
    package.content = parse_content(
        "\n".join(f"## Тема {i}\nКоманда проверяет этап {i}." for i in range(count))
    )
    package.constraints = parse_constraints(None, "", "", preset)
    # Build count scenes independently of the upper-bound planner cap so an excess
    # still exercises the publication gate.
    package.constraints.slides = count
    scenes = compose_variant(extractive_plans(package).variants[0], package)
    package.constraints = parse_constraints(None, "", "", preset)
    assert any(f.code == "slide_count" for f in audit_scenes(scenes, package)) is blocked


def test_explicit_count_overrides_preset_range(prepared):
    _, _, package = prepared
    package.analysis = {}
    package.control.slide_budget = None
    package.content = parse_content(
        "Первый этап завершён. Второй этап проверен. Третий этап принят."
    )
    package.constraints = parse_constraints(5, "", "", "mini")
    scenes = compose_variant(extractive_plans(package).variants[0], package)
    assert len(scenes) == 3
    assert any(f.code == "slide_count" for f in audit_scenes(scenes, package))
