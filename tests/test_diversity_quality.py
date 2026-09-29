from types import SimpleNamespace

import pytest

from studio.checks import diversity
from studio.models import Box, Element, Finding, Pattern, SlideScene


def fixture_scene():
    zones = [Box(x=20 + i * 210, y=100, w=200, h=160) for i in range(3)]
    pattern = Pattern(
        id="cards",
        source_slide=1,
        source_layout="Three cards",
        text_zones=zones,
        body_zones=zones[:2],
        role="content",
    )
    package = SimpleNamespace(template=SimpleNamespace(patterns=[pattern], width=960, height=540))
    scene = SlideScene(
        title="Results",
        background="#ffffff",
        layout="table",
        purpose="metrics",
        pattern_id=pattern.id,
        source_ids=["f1", "f2"],
        elements=[
            Element(
                kind="table",
                box=zones[0],
                size=20,
                source_ids=["f1"],
                rows=[["A", "B"], ["1", "2"]],
            ),
            Element(kind="text", box=zones[1], size=20, source_ids=["f2"], text="Summary"),
        ],
    )
    return package, scene, zones


def test_added_empty_card_is_rejected(monkeypatch):
    package, scene, zones = fixture_scene()
    monkeypatch.setattr(diversity, "audit_scenes", lambda *_: [])
    alternate = package.template.patterns[0].model_copy(
        deep=True, update={"id": "extra", "body_zones": zones}
    )
    package.template.patterns.append(alternate)
    candidate = scene.model_copy(deep=True, update={"pattern_id": "extra"})
    assert diversity.unused_body_regions(scene, package) == 0
    assert diversity.unused_body_regions(candidate, package) == 1
    assert not diversity.preserves_quality([scene], [candidate], package)


def test_image_fills_content_slot_but_title_or_background_does_not():
    package, scene, zones = fixture_scene()
    scene.elements = scene.elements[:1]
    for role in ("title", "template_background"):
        scene.elements.append(Element(kind="text", role=role, box=zones[1], source_ids=["f2"]))
    assert diversity.unused_body_regions(scene, package) == 1
    scene.elements.append(Element(kind="image", box=zones[1], image_id="upload-1"))
    assert diversity.unused_body_regions(scene, package) == 0


def test_large_removed_source_panel_counts_but_footer_does_not():
    package, scene, _ = fixture_scene()
    package.template.patterns[0].fields = [
        {"role": "unused", "box": {"x": 500, "y": 60, "w": 360, "h": 420}},
        {"role": "unused", "box": {"x": 30, "y": 510, "w": 100, "h": 15}},
    ]
    assert diversity.unused_body_regions(scene, package) == 1


@pytest.mark.parametrize("purpose", ["cover", "divider"])
def test_authored_cover_and_divider_whitespace_is_not_penalized(purpose):
    package, scene, _ = fixture_scene()
    scene.purpose = purpose
    scene.elements = []
    assert diversity.unused_body_regions(scene, package) == 0


@pytest.mark.parametrize("kind", ["text", "table", "chart"])
def test_new_caption_sized_evidence_rejected(monkeypatch, kind):
    package, scene, _ = fixture_scene()
    monkeypatch.setattr(diversity, "audit_scenes", lambda *_: [])
    scene.elements[0].kind = kind
    candidate = scene.model_copy(deep=True)
    candidate.elements[0].size = 12
    assert not diversity.preserves_quality([scene], [candidate], package)
    candidate.elements[0].size = 16
    assert diversity.preserves_quality([scene], [candidate], package)
    scene.elements[0].size = 12
    candidate.elements[0].size = 12
    assert diversity.preserves_quality([scene], [candidate], package)
    candidate.elements[0].size = 10
    assert not diversity.preserves_quality([scene], [candidate], package)


def test_new_contrast_warning_rejected(monkeypatch):
    package, scene, _ = fixture_scene()
    candidate = scene.model_copy(deep=True)
    candidate.elements[0].color = "#cccccc"
    monkeypatch.setattr(
        diversity,
        "audit_scenes",
        lambda scenes, _: (
            [Finding(code="contrast", severity="warning", message="low contrast", slide=1)]
            if scenes[0].elements[0].color == "#cccccc"
            else []
        ),
    )
    assert not diversity.preserves_quality([scene], [candidate], package)
    assert diversity.preserves_quality([candidate], [scene], package)


def test_grouped_fact_aliases_cannot_hide_smaller_type(monkeypatch):
    package, scene, _ = fixture_scene()
    monkeypatch.setattr(diversity, "audit_scenes", lambda *_: [])
    scene.elements[0].source_ids = ["f1", "cell-alias"]
    candidate = scene.model_copy(deep=True)
    candidate.elements[0].source_ids = ["f1"]
    candidate.elements[0].size = 12
    assert not diversity.preserves_quality([scene], [candidate], package)


def test_safe_in_zone_reflow_allowed(monkeypatch):
    package, scene, _ = fixture_scene()
    monkeypatch.setattr(diversity, "audit_scenes", lambda *_: [])
    candidate = scene.model_copy(deep=True)
    candidate.elements[0].box.w *= 0.84
    assert diversity.preserves_quality([scene], [candidate], package)


def test_evidence_cannot_be_squeezed_into_a_small_fraction_of_its_area(monkeypatch):
    package, scene, _ = fixture_scene()
    monkeypatch.setattr(diversity, "audit_scenes", lambda *_: [])
    candidate = scene.model_copy(deep=True)
    candidate.elements[0].box.h *= 0.69
    assert not diversity.preserves_quality([scene], [candidate], package)


def test_all_diversity_paths_respect_quality_guard(prepared, monkeypatch):
    from studio.composition.composer import compose_variant
    from studio.contents.planner import extractive_plans

    _, _, package = prepared
    original = compose_variant(extractive_plans(package).variants[0], package)
    decks = {
        key: [s.model_copy(deep=True) for s in original]
        for key in ("executive", "analytical", "story")
    }
    monkeypatch.setattr(diversity, "preserves_quality", lambda *_: False)
    report = diversity.ensure_diversity(decks, package)
    assert not report["verified"]
    assert not report["adjustments"]
    assert all(scenes == original for scenes in decks.values())
