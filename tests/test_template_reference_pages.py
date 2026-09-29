from types import SimpleNamespace

from studio.templates.template_analysis import exclude_reference_pages, reference_page_evidence


def test_style_guide_samples_are_excluded_without_discarding_other_layouts():
    inventory = {
        "slides": [
            {
                "slide": 1,
                "sample": "Default Settings\nTheme Fonts: Arial\nText Box Default: Arial 16pt\n"
                "Drawing Default Styles:\nSample\nSample\nSample",
            },
            {
                "slide": 2,
                "sample": "Color Scheme\nAccent 1\nR178 G183 B187\nAccent 2\nR0 G46 B109\n"
                "Accent 3\nR94 G115 B97",
            },
            {"slide": 3, "sample": "Typography\nOur team improved readability with Arial."},
        ]
    }
    patterns = [
        SimpleNamespace(id=f"native-slide-{i}", source_slide=i, purpose="unknown", reusable=True)
        for i in range(1, 4)
    ]
    patterns.append(
        SimpleNamespace(id="native-layout-0-1", source_slide=0, purpose="unknown", reusable=True)
    )
    report = exclude_reference_pages(SimpleNamespace(patterns=patterns), inventory)

    assert [(row["pattern_id"], row["evidence"]) for row in report] == [
        ("native-slide-1", "style_defaults_with_samples"),
        ("native-slide-2", "palette_with_rgb_swatches"),
    ]
    assert [(p.purpose, p.reusable) for p in patterns] == [
        ("reference", False),
        ("reference", False),
        ("unknown", True),
        ("unknown", True),
    ]


def test_normal_typography_content_is_not_a_reference_page():
    assert (
        reference_page_evidence(
            "Typography\nTheme fonts improve the reading experience.\n"
            "Our design uses three sample paragraphs to demonstrate accessible choices."
        )
        is None
    )
    assert (
        reference_page_evidence(
            "Color Scheme\nThe project compares RGB values in three datasets.\n"
            "Accent colors are considered in the analysis."
        )
        is None
    )
