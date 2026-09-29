"""Editorial preference must not block an explicitly chosen native layout."""

from studio.checks.quality import meaningful_diversity
from studio.composition.composer import compose
from studio.composition.contracts import candidates
from studio.models import Box, Pattern, SlidePlan


def _package(prepared):
    package = prepared[2]
    package.analysis["editorial"] = {"status": "ready"}
    package.constraints.summarize = True
    title = Box(x=40, y=30, w=800, h=60)
    left = Box(x=50, y=135, w=350, h=280)
    right = Box(x=510, y=135, w=350, h=280)
    package.template.patterns = [
        Pattern(
            id="authored",
            source_slide=1,
            source_layout="Source slide",
            text_zones=[title, left],
            title_zone=title,
            body_zones=[left],
            role="statement",
            fields=[
                {"role": "title", "index": 0, "shape_id": 2},
                {"role": "body", "index": 0, "shape_id": 3},
            ],
        ),
        Pattern(
            id="layout-only",
            source_slide=0,
            source_layout="Alternate layout",
            text_zones=[title, right],
            title_zone=title,
            body_zones=[right],
            role="statement",
        ),
    ]
    return package


def _slide(package, index, pattern_id=None):
    return SlidePlan(
        title=f"Slide {index + 1}",
        fact_ids=[package.content.facts[index].id],
        layout="statement",
        pattern_id=pattern_id,
    )


def test_editorial_prefers_authored_only_for_automatic_selection(prepared):
    package = _package(prepared)
    automatic = _slide(package, 0)
    explicit = _slide(package, 0, "layout-only")

    assert [p.id for p in candidates(package, automatic)] == ["authored"]
    assert {p.id for p in candidates(package, explicit)} == {"authored", "layout-only"}
    assert [p.id for p in candidates(package, explicit, source_slides_only=True)] == ["authored"]
    package.template.patterns[1].reusable = False
    assert [p.id for p in candidates(package, explicit)] == ["authored"]


def test_explicit_layout_relayout_creates_source_linked_diversity(prepared):
    package = _package(prepared)
    original = [compose(_slide(package, i), package, i, "executive") for i in range(5)]
    same = [scene.model_copy(deep=True) for scene in original]
    assert not meaningful_diversity({"executive": original, "story": same}, package.template)[
        "verified"
    ]

    story = [
        compose(_slide(package, i, "layout-only" if i < 3 else None), package, i, "story")
        for i in range(5)
    ]
    result = meaningful_diversity({"executive": original, "story": story}, package.template)

    assert [scene.pattern_id for scene in story[:3]] == ["layout-only"] * 3
    assert [scene.source_ids for scene in story] == [scene.source_ids for scene in original]
    assert result["verified"]
    assert result["pairs"][0]["changed_slides"] == [1, 2, 3]
