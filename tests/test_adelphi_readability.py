from types import SimpleNamespace

from PIL import Image, ImageDraw

from studio.composition.text_composer import fact_elements, text_element
from studio.config import ROOT
from studio.contents.parsing import parse_content
from studio.models import Box, Pattern
from studio.templates.template_adaptation import derive_data_patterns


def test_body_text_tries_readable_size_without_exceeding_field():
    profile = SimpleNamespace(
        font="Montserrat",
        font_file=str(ROOT / "fonts" / "Montserrat-Regular.ttf"),
        font_roles={},
        font_assets=[],
        font_sizes=[11, 12, 14, 19, 28],
        body_size=12,
        height=540,
        foreground="#000000",
    )
    text = "Unified queue pilot across offices"
    roomy = Box(x=0, y=0, w=200, h=40)
    tight = Box(x=0, y=0, w=200, h=32)
    assert text_element(text, roomy, profile, size=12).size == 16
    assert fact_elements(parse_content(text).facts, roomy, profile, "#000000")[0].size == 16
    assert text_element(text, tight, profile, size=12).size == 12
    assert fact_elements(parse_content(text).facts, tight, profile, "#000000")[0].size == 12


def _two_column_pattern(path):
    title = Box(x=43.2, y=25.2, w=633.6, h=86.4)
    zones = [
        Box(x=43.2, y=129.6, w=303.6, h=331.7),
        Box(x=373.2, y=129.6, w=303.6, h=331.7),
    ]
    pattern = Pattern(
        id="native-slide-2",
        source_slide=2,
        source_layout="content",
        role="content",
        purpose="content",
        text_zones=[title, *zones],
        title_zone=title,
        body_zones=zones,
        background_image=str(path),
        heading_zones=[None, None],
        number_zones=[None, None],
        fields=[{"role": "title", "index": 0, "box": title.model_dump()}]
        + [{"role": "body", "index": i, "box": zone.model_dump()} for i, zone in enumerate(zones)],
    )
    return pattern


def test_horizontal_data_region_requires_plain_gap_and_no_semantic_graphic(tmp_path):
    artwork = tmp_path / "art.png"
    image = Image.new("RGB", (720, 540), "white")
    ImageDraw.Draw(image).rectangle((0, 477, 720, 540), fill="#ffd600")
    image.save(artwork)
    pattern = _two_column_pattern(artwork)
    source = pattern.model_dump()
    profile = SimpleNamespace(patterns=[pattern], width=720, height=540, margin=40)
    assert [item["id"] for item in derive_data_patterns(profile)] == ["data-native-slide-2"]
    derived = profile.patterns[-1]
    assert derived.body_zones[0].w >= 633.5
    assert pattern.model_dump() == source

    # A one-pixel divider is below the ordinary 0.5% image-uniformity tolerance.
    # It still marks two distinct authored panels and must block the union.
    ImageDraw.Draw(image).line((360, 130, 360, 460), fill="#ffd600", width=1)
    image.save(artwork)
    profile.patterns = [pattern]
    assert not derive_data_patterns(profile)

    # A solid, one-pixel divider fills its own gap uniformly. Check the
    # complete merged band as well as the gap so it cannot erase this artwork.
    image = Image.new("RGB", (720, 540), "white")
    ImageDraw.Draw(image).line((347, 130, 347, 461), fill="#ffd600", width=1)
    image.save(artwork)
    pattern.body_zones[1] = Box(x=347.8, y=129.6, w=329, h=331.7)
    assert not derive_data_patterns(profile)

    image = Image.new("RGB", (720, 540), "white")
    image.save(artwork)
    pattern.graphic_kind = "comparison"
    assert not derive_data_patterns(profile)


def test_verified_data_union_does_not_report_its_consumed_source_field():
    from studio.checks.scene_regions import unused_body_regions
    from studio.models import Element, SlideScene

    merged = Box(x=40, y=120, w=500, h=300)
    consumed = Box(x=290, y=120, w=250, h=300)
    outside = Box(x=550, y=120, w=150, h=300)
    pattern = Pattern(
        id="data-native",
        source_slide=1,
        source_layout="content",
        role="content",
        purpose="content",
        text_zones=[merged],
        body_zones=[merged],
        safe_text_zone={
            "data_region": merged.model_dump(),
            "data_region_method": "authored_field_band_expanded_with_uniform_background_guard",
        },
        fields=[{"role": "unused", "box": consumed.model_dump()}],
    )
    package = SimpleNamespace(template=SimpleNamespace(patterns=[pattern], width=720, height=540))
    content = Element(kind="text", box=Box(x=40, y=120, w=200, h=80), source_ids=["f1"])
    scene = SlideScene(
        title="Data",
        background="#FFFFFF",
        elements=[content],
        source_ids=["f1"],
        layout="table",
        purpose="content",
        pattern_id=pattern.id,
    )
    assert unused_body_regions(scene, package) == 0
    assert unused_body_regions(scene.model_copy(update={"elements": []}), package) > 0
    pattern.safe_text_zone.clear()
    assert unused_body_regions(scene, package) == 1
    pattern.safe_text_zone = {
        "data_region": merged.model_dump(),
        "data_region_method": "authored_field_band_expanded_with_uniform_background_guard",
    }
    pattern.fields.append({"role": "unused", "box": outside.model_dump()})
    assert unused_body_regions(scene, package) == 1
