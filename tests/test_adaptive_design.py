from types import SimpleNamespace

import pytest

from studio.models import Box, Element, Fact, Pattern, SlideScene
from studio.design_balance import improve_contrast, composition_family, rhythm_cost, design_cost
from studio.chart_space import expand_chart_space
from studio.text_composer import text_element, fact_elements
from studio.fonts import wrap_text, element_font
from studio.template import analyze_template


@pytest.fixture
def profile(template, tmp_path):
    p = analyze_template(template, tmp_path / "profile")
    p.width, p.height, p.body_size, p.title_size = 720, 405, 16, 24
    return p


def test_sparse_text_grows_and_dense_text_keeps_all_evidence(profile):
    zone = Box(x=40, y=100, w=550, h=170)
    facts = [
        Fact(id="a", text="Первый этап: проверка заявки."),
        Fact(id="b", text="Второй этап: выполнение работы."),
    ]
    sparse = fact_elements(facts, zone, profile, profile.foreground)
    assert min(e.size for e in sparse) > 16
    assert [e.source_ids for e in sparse] == [["a"], ["b"]]
    dense = fact_elements(facts * 4, zone, profile, profile.foreground)
    assert [e.text for e in dense] == [f.text for f in facts * 4]
    assert max(e.size for e in dense) <= min(e.size for e in sparse)
    for elements in (sparse, dense):
        for e in elements:
            assert e.box.x >= zone.x and e.box.y + e.box.h <= zone.y + zone.h + 0.5
        assert all(a.box.y + a.box.h <= b.box.y for a, b in zip(elements, elements[1:]))


def test_title_growth_is_measured_and_source_field_is_immutable(profile):
    zone = Box(x=40, y=20, w=500, h=65)
    before = zone.model_dump()
    e = text_element("Результат пилота", zone, profile, "title", size=24)
    assert e.size > 24 and zone.model_dump() == before
    assert (
        len(wrap_text(e.text, element_font(profile, e)[1], e.size, zone.w * 0.94)) * e.size * 1.25
        <= zone.h
    )
    narrow = text_element(
        "Проверка результата обработки заявки",
        Box(x=40, y=20, w=260, h=65),
        profile,
        "title",
        size=24,
    )
    assert narrow.size < e.size and narrow.text == "Проверка результата обработки заявки"


def test_body_contrast_uses_local_background_and_template_palette():
    p = SimpleNamespace(colors=["#0077FF", "#FFFFFF", "#212121"], foreground="#212121")
    e = Element(
        kind="text",
        box=Box(x=0, y=0, w=100, h=100),
        text="Данные",
        role="body",
        size=22,
        color="#0077FF",
        background_hint="#212121",
    )
    title = e.model_copy(update={"role": "title"})
    scene = SlideScene(
        layout="columns", title="Тест", background="#FFFFFF", elements=[e, title], source_ids=[]
    )
    improve_contrast(scene, p)
    assert e.color == "#FFFFFF" and title.color == "#0077FF"
    assert e.text == "Данные" and e.background_hint == "#212121"


def test_chart_grows_only_inside_its_region_and_stops_before_caption():
    zone = Box(x=40, y=90, w=500, h=270)
    pattern = Pattern(
        id="source",
        source_slide=1,
        source_layout="test",
        text_zones=[],
        role="content",
        body_zones=[zone],
    )
    profile = SimpleNamespace(width=720, height=405, margin=30, patterns=[pattern])
    chart = Element(
        kind="chart",
        box=Box(x=40, y=90, w=500, h=120),
        values=[1, 2],
        labels=["A", "B"],
        source_ids=["data"],
    )
    caption = Element(
        kind="text",
        box=Box(x=40, y=320, w=500, h=25),
        text="Оговорка",
        role="body",
        source_ids=["note"],
    )
    scene = SlideScene(
        layout="chart",
        title="Тест",
        background="#FFFFFF",
        elements=[chart, caption],
        source_ids=["data", "note"],
        pattern_id="source",
        strategy="native_template",
    )
    snapshot = scene.model_dump()
    result = expand_chart_space(scene, SimpleNamespace(template=profile))
    assert scene.model_dump() == snapshot
    assert result.elements[0].box.h == 218
    assert result.elements[0].values == [1, 2] and result.elements[1] == caption
    assert result.elements[0].box.y + result.elements[0].box.h < caption.box.y


def test_design_and_rhythm_measure_content_not_background(profile):
    a = SlideScene(
        layout="columns",
        title="A",
        background="#FFFFFF",
        source_ids=["a"],
        elements=[
            Element(
                kind="text",
                box=Box(x=30, y=100, w=300, h=60),
                size=16,
                role="body",
                text="A",
                source_ids=["a"],
            )
        ],
    )
    b = a.model_copy(deep=True)
    b.elements[0].box.w = 600
    b.elements[0].size = 22
    assert design_cost(b, profile) < design_cost(a, profile)
    x, y = composition_family(a, profile), composition_family(b, profile)
    assert rhythm_cost([x, x, x, x]) > rhythm_cost([x, y, x, y])
    b.background = "#000000"
    assert composition_family(b, profile) == y


def test_diversity_cannot_split_evidence_words_or_shrink_charts(profile):
    from studio.quality import candidate_regressions
    from studio.diversity import preserves_quality
    from studio.fonts import text_width

    text = "Координатор отвечает за результат."
    width = text_width("Координатор", profile.font_file, 27) + 4
    e = Element(
        kind="text",
        box=Box(x=30, y=80, w=width, h=240),
        size=27,
        role="body",
        text=text,
        source_ids=["a"],
    )
    original = SlideScene(
        title="A", background="#FFFFFF", layout="columns", source_ids=["a"], elements=[e]
    )
    changed = original.model_copy(deep=True)
    changed.elements[0].box.w *= 0.72
    package = SimpleNamespace(template=profile)
    issues = candidate_regressions([original], [changed], package, audit=lambda *_: [])
    assert {p["code"] for p in issues} == {"word_break_regression"}
    chart = Element(kind="chart", box=Box(x=30, y=80, w=500, h=240), source_ids=["data"])
    original.elements = [chart]
    changed = original.model_copy(deep=True)
    changed.elements[0].box.w *= 0.84
    assert not preserves_quality([original], [changed], package)


def test_removed_card_counts_even_below_eight_percent_of_slide():
    from studio.scene_regions import unused_body_regions

    body = Box(x=430, y=220, w=450, h=125)
    pattern = Pattern(
        id="cards",
        source_slide=1,
        source_layout="test",
        role="content",
        text_zones=[],
        body_zones=[body],
        fields=[{"role": "unused", "box": {"x": 430, "y": 120, "w": 450, "h": 75}}],
    )
    profile = SimpleNamespace(width=960, height=540, patterns=[pattern])
    scene = SlideScene(
        title="A",
        background="#FFFFFF",
        layout="columns",
        pattern_id="cards",
        source_ids=["a"],
        elements=[Element(kind="text", box=body, text="A", source_ids=["a"], role="body")],
    )
    assert unused_body_regions(scene, SimpleNamespace(template=profile)) == 1
    pattern.fields[0]["box"]["h"] = 15
    assert unused_body_regions(scene, SimpleNamespace(template=profile)) == 0


def test_reflow_fits_whole_words_without_mutating_evidence_or_chart(profile):
    from studio.design_balance import fit_reflow_words, broken_words
    from studio.fonts import text_width

    width = text_width("Координатор", profile.font_file, 27) * 0.75
    text = Element(
        kind="text",
        box=Box(x=40, y=80, w=width, h=240),
        text="Координатор отвечает за результат.",
        role="body",
        size=27,
        source_ids=["fact"],
    )
    chart = Element(
        kind="chart",
        box=Box(x=300, y=80, w=350, h=240),
        values=[10, 20],
        labels=["A", "B"],
        source_ids=["data"],
    )
    scene = SlideScene(
        title="A",
        background="#FFFFFF",
        layout="columns",
        source_ids=["fact", "data"],
        elements=[text, chart],
    )
    chart_before = chart.model_dump()
    text_before = (text.text, text.source_ids[:], text.box.model_dump())
    assert broken_words(scene, profile) > 0
    fit_reflow_words(scene, profile)
    assert broken_words(scene, profile) == 0
    assert 16 <= text.size < 27
    assert (text.text, text.source_ids, text.box.model_dump()) == text_before
    assert chart.model_dump() == chart_before
