from types import SimpleNamespace
from studio.models import Pattern, Box, SlidePlan, SlideScene, VariantPlan
from studio.checks.background_diversity import artwork_family, sequence_cost, diversify_backgrounds


def test_same_artwork_is_one_family_despite_different_ids_and_paths(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    a.write_bytes(b"same artwork")
    b.write_bytes(a.read_bytes())
    p = Pattern(
        source_layout="test",
        text_zones=[],
        role="content",
        id="native",
        source_slide=1,
        background_image=str(a),
    )
    q = p.model_copy(update={"id": "derived", "source_slide": 2, "background_image": str(b)})
    assert artwork_family(p) == artwork_family(q)
    b.write_bytes(b"different artwork")
    assert artwork_family(p) != artwork_family(q)
    assert sequence_cost(["a", "b", "c", "b", "c", "b"]) < sequence_cost(
        ["a", "b", "b", "b", "b", "b"]
    )


def test_selector_keeps_content_and_rejects_unsafe_alternatives(monkeypatch, tmp_path):
    from studio.checks import audit
    from studio.checks import quality
    from studio.composition import composer
    from studio.composition import contracts

    patterns = []
    for i in range(4):
        path = tmp_path / f"{i}.png"
        path.write_bytes(str(i).encode())
        patterns.append(
            Pattern(
                source_layout="test",
                text_zones=[],
                role="content",
                id=f"p{i}",
                source_slide=i + 1,
                background_image=str(path),
            )
        )
    package = SimpleNamespace(template=SimpleNamespace(patterns=patterns))
    variant = VariantPlan(
        key="executive",
        title="Тест",
        slides=[
            SlidePlan(
                title=f"Слайд {i}",
                fact_ids=[f"f{i}"],
                layout="columns",
                purpose="cover" if i == 0 else "content",
                pattern_id="p0" if i == 0 else "p1",
            )
            for i in range(6)
        ],
    )
    snapshot = variant.model_dump()

    def compose(v, p):
        return [
            SlideScene(
                title=s.title,
                background="#FFFFFF",
                elements=[],
                source_ids=s.fact_ids,
                purpose=s.purpose,
                layout=s.layout,
                pattern_id=s.pattern_id,
                strategy="native_template",
            )
            for s in v.slides
        ]

    class Session:
        def __init__(self, p):
            self.package = p

        def variant(self, v):
            return compose(v, self.package)

        def slide(self, v, i):
            return compose(v, self.package)[i]

    monkeypatch.setattr(composer, "CompositionSession", Session)
    monkeypatch.setattr(contracts, "candidates", lambda *args, **kwargs: patterns[1:])
    monkeypatch.setattr(audit, "repair_scenes", lambda *args: [])
    monkeypatch.setattr(
        quality,
        "candidate_regressions",
        lambda old, new, p, **kwargs: (
            [{"code": "readability_regression"}] if new[0].pattern_id == "p3" else []
        ),
    )
    result, scenes, report = diversify_backgrounds(variant, package)
    assert variant.model_dump() == snapshot
    assert report["after"]["unique_backgrounds"] == 3
    assert report["after"]["largest_use"] < report["before"]["largest_use"]
    assert report["after"]["adjacent_repeats"] == 0
    assert result.slides[0] == variant.slides[0]
    for old, new in zip(variant.slides, result.slides):
        assert old.model_dump(exclude={"pattern_id"}) == new.model_dump(exclude={"pattern_id"})
        assert new.pattern_id != "p3"
    monkeypatch.setattr(
        quality, "candidate_regressions", lambda *args, **kwargs: [{"code": "overflow"}]
    )
    kept, _, limited = diversify_backgrounds(variant, package)
    assert kept == variant and not limited["changes"]
    assert limited["slides_without_safe_alternative"] == [1, 2, 3, 4, 5, 6]


def test_roomy_fields_preserve_title_and_stop_at_artwork(tmp_path):
    from PIL import Image, ImageDraw
    from studio.templates.template_adaptation import derive_roomy_text_patterns, uniform_region

    path = tmp_path / "art.png"
    im = Image.new("RGB", (720, 405), "blue")
    ImageDraw.Draw(im).rectangle((280, 200, 720, 405), fill="pink")
    im.save(path)
    title = Box(x=40, y=100, w=240, h=70)
    body = Box(x=40, y=220, w=180, h=100)
    pattern = Pattern(
        source_layout="test",
        text_zones=[],
        role="content",
        id="native",
        source_slide=1,
        purpose="content",
        title_zone=title,
        body_zones=[body],
        background_image=str(path),
        fields=[
            {"role": "title", "index": 0, "box": title.model_dump()},
            {"role": "body", "index": 0, "box": body.model_dump()},
        ],
    )
    profile = SimpleNamespace(patterns=[pattern], width=720, height=405)
    old = pattern.model_dump()
    assert derive_roomy_text_patterns(profile)
    expanded = profile.patterns[-1]
    assert pattern.model_dump() == old and expanded.title_zone == title
    assert expanded.body_zones[0].w > body.w and uniform_region(
        path, expanded.body_zones[0], 720, 405
    )
    assert not derive_roomy_text_patterns(profile)
