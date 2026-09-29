"""Synthetic evidence objects only: structure must reflect what a reader sees."""

from copy import deepcopy
from types import SimpleNamespace

from studio.checks.diversity import ensure_diversity, geometry_signature
from studio.checks.quality import meaningful_diversity
from studio.models import Box, Element, SlideScene


PROFILE = SimpleNamespace(width=960, height=540)


def slide():
    return SlideScene(
        title="Summary",
        background="#fff",
        layout="content",
        purpose="content",
        source_ids=["one", "two", "three"],
        elements=[
            Element(
                kind="text",
                source_ids=["one"],
                text="First",
                size=20,
                box=Box(x=60, y=110, w=220, h=100),
            ),
            Element(
                kind="text",
                source_ids=["two"],
                text="Second",
                size=20,
                box=Box(x=360, y=110, w=220, h=100),
            ),
            Element(
                kind="text",
                source_ids=["three"],
                text="Third",
                size=20,
                box=Box(x=660, y=110, w=220, h=100),
            ),
        ],
    )


def different(a, b):
    return meaningful_diversity({"a": [a], "b": [b]}, PROFILE)["verified"]


def test_position_size_and_styling_cannot_fake_structure():
    original = slide()
    shifted = deepcopy(original)
    for element in shifted.elements:
        element.box.x += 80
        element.box.y += 70
        element.box.w *= 0.7
        element.size = 17
        element.color = "#f00"
        element.text = "Different wording"
    shifted.layout = "process"
    shifted.pattern_id = "other"
    assert not different(original, shifted)
    assert geometry_signature([original]) == geometry_signature([shifted])


def test_resizing_cannot_flip_a_reading_relationship():
    original = slide()
    original.elements = original.elements[:2]
    original.source_ids = ["one", "two"]
    original.elements[0].box = Box(x=40, y=60, w=200, h=200)
    original.elements[1].box = Box(x=140, y=160, w=200, h=200)
    resized = deepcopy(original)
    # Former width/height-relative thresholds flipped at 100 < 0.35 * 400.
    resized.elements[0].box.w = 400
    resized.elements[0].box.h = 400
    assert not different(original, resized)
    assert geometry_signature([original]) == geometry_signature([resized])

    vertical = deepcopy(original)
    vertical.elements[1].box.x = 40
    assert different(original, vertical)


def test_reading_relationship_and_grouping_count():
    original = slide()
    reordered = deepcopy(original)
    reordered.elements[0].box.x, reordered.elements[2].box.x = (
        reordered.elements[2].box.x,
        reordered.elements[0].box.x,
    )
    assert different(original, reordered)
    stacked = deepcopy(original)
    for i, element in enumerate(stacked.elements):
        element.box.x, element.box.y = 60, 110 + 130 * i
    assert different(original, stacked)
    grouped = deepcopy(original)
    grouped.elements[0].source_ids = ["one", "two"]
    grouped.elements.pop(1)
    assert different(original, grouped)


def test_actual_table_shape_counts_but_cell_wording_does_not():
    original = slide()
    original.elements[0].kind = "table"
    original.elements[0].rows = [["A", "B"], ["1", "2"]]
    relabeled = deepcopy(original)
    relabeled.elements[0].rows = [["X", "Y"], ["3", "4"]]
    assert not different(original, relabeled)
    transposed = deepcopy(original)
    transposed.elements[0].rows = [["A", "1"], ["B", "2"], ["C", "3"]]
    assert different(original, transposed)
    chart = deepcopy(original)
    chart.elements[0].kind = "chart"
    assert different(original, chart)


def test_report_is_honest_and_never_mutates_constrained_decks():
    original = [slide()]
    decks = {key: deepcopy(original) for key in ("executive", "analytical", "story")}
    package = SimpleNamespace(template=PROFILE)
    report = ensure_diversity(decks, package)
    assert not report["verified"]
    assert report["distinct"] == 1 and report["expected"] == 3
    assert report["adjustments"] == [] and report["findings"]
    assert all(scenes == original for scenes in decks.values())


def test_distinct_counts_pairwise_verified_variants_not_unique_hashes():
    original = [slide() for _ in range(10)]
    one_changed = deepcopy(original)
    one_changed[0].elements[0].box.x, one_changed[0].elements[2].box.x = (
        one_changed[0].elements[2].box.x,
        one_changed[0].elements[0].box.x,
    )
    another_changed = deepcopy(original)
    another_changed[1].elements[0].box.x, another_changed[1].elements[2].box.x = (
        another_changed[1].elements[2].box.x,
        another_changed[1].elements[0].box.x,
    )
    package = SimpleNamespace(template=PROFILE)
    decks = {"executive": original, "analytical": one_changed, "story": another_changed}
    report = ensure_diversity(decks, package)
    assert len(set(report["signatures"].values())) == 3
    assert not report["verified"] and report["distinct"] == 1

    # Rebuild cleanly: two variants differ from the baseline on 5 slides,
    # but differ from each other on only one slide.
    one_changed = deepcopy(original)
    for i in range(5):
        one_changed[i].elements[0].box.x, one_changed[i].elements[2].box.x = (
            one_changed[i].elements[2].box.x,
            one_changed[i].elements[0].box.x,
        )
    another_changed = deepcopy(one_changed)
    another_changed[5].elements[0].box.x, another_changed[5].elements[2].box.x = (
        another_changed[5].elements[2].box.x,
        another_changed[5].elements[0].box.x,
    )
    report = ensure_diversity(
        {"executive": original, "analytical": one_changed, "story": another_changed}, package
    )
    assert not report["verified"] and report["distinct"] == 2


def test_empty_and_single_variant_count():
    package = SimpleNamespace(template=PROFILE)
    assert ensure_diversity({}, package)["distinct"] == 0
    report = ensure_diversity({"only": [slide()]}, package)
    assert report["distinct"] == 1 and report["verified"]
    assert report["expected"] == 1 and report["findings"] == []


def test_two_claim_visual_organizations_need_real_object_changes():
    lead = SlideScene(
        title="Two claims",
        background="#fff",
        layout="content",
        purpose="content",
        source_ids=["a", "b"],
        elements=[
            Element(kind="text", source_ids=["a"], text="Lead", box=Box(x=40, y=100, w=470, h=300)),
            Element(
                kind="text", source_ids=["b"], text="Detail", box=Box(x=540, y=100, w=340, h=300)
            ),
        ],
    )
    columns = deepcopy(lead)
    columns.elements[0].box.w = 340
    columns.elements[1].box.x = 480
    assert not different(lead, columns)  # equal columns are still two side-by-side claims
    comparison = deepcopy(lead)
    comparison.elements = [
        Element(
            kind="table",
            source_ids=["a", "b"],
            rows=[["Aspect", "Value"], ["A", "Lead"], ["B", "Detail"]],
            box=Box(x=40, y=100, w=840, h=300),
        )
    ]
    narrative = deepcopy(lead)
    narrative.elements = [
        Element(
            kind="text",
            source_ids=["a", "b"],
            text="Lead. Detail.",
            box=Box(x=40, y=100, w=840, h=300),
        )
    ]
    decks = {"executive": [lead], "analytical": [comparison], "story": [narrative]}
    report = ensure_diversity(decks, SimpleNamespace(template=PROFILE))
    assert report["verified"] and report["distinct"] == 3


def test_cosmetic_layout_choices_cannot_erase_a_working_structure(template, tmp_path):
    from types import SimpleNamespace
    from studio.models import Box, Element, SlideScene, Finding
    from studio.checks.quality import candidate_regressions

    original = SlideScene(
        title="Example",
        layout="statement",
        background="#FFFFFF",
        source_ids=["a", "b"],
        elements=[
            Element(
                kind="text",
                box=Box(x=30, y=50, w=400, h=160),
                text="Two source statements",
                source_ids=["a", "b"],
                size=20,
            )
        ],
    )
    split = original.model_copy(deep=True)
    split.elements = [
        Element(
            kind="text",
            box=Box(x=30 + i * 220, y=50, w=200, h=160),
            text="Source statement",
            source_ids=[fid],
            size=20,
        )
        for i, fid in enumerate(["a", "b"])
    ]
    from studio.templates.parsing import analyze_template

    package = SimpleNamespace(template=analyze_template(template, tmp_path / "profile"))

    def clean(scenes, package):
        return []

    assert candidate_regressions([original], [split], package, audit=clean) == []
    assert {
        p["code"]
        for p in candidate_regressions(
            [original], [split], package, audit=clean, preserve_structure=True
        )
    } == {"composition_changed"}

    def needs_repair(scenes, package):
        return (
            [Finding(code="text_overflow", severity="error", slide=1, message="Synthetic")]
            if scenes[0] is original
            else []
        )

    assert (
        candidate_regressions(
            [original], [split], package, audit=needs_repair, preserve_structure=True
        )
        == []
    )
