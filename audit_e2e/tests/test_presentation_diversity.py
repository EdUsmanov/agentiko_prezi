from copy import deepcopy

import pytest

from audit_e2e.presentation_diversity import assess_presentation_diversity


QUOTES = [
    "Intake is recorded.",
    "Review takes two days.",
    "Approval is conditional.",
    "The owner checks delivery.",
]


def _object(indices, x, y, width=4, height=1):
    return {
        "name": "source block " + str(indices),
        "text": " ".join(QUOTES[i] for i in indices),
        "visibility": "visible",
        "geometry_inches": {"x": x, "y": y, "width": width, "height": height},
    }


def _bundle(layouts):
    variants = {}
    for name, layout in zip(("executive", "analytical", "story"), layouts):
        variants[name] = {
            "slides": [
                {"number": 1, "text": "Common source title", "objects": []},
                {"number": 2, "text": " ".join(QUOTES), "objects": layout},
            ]
        }
    return {
        "expected_variant_ids": list(variants),
        "reference": {
            "points": [{"id": f"p{i}", "quote": q, "required": True} for i, q in enumerate(QUOTES)]
        },
        "variants": variants,
    }


LAYOUTS = [
    [_object([0, 1], 1, 1), _object([2, 3], 1, 3)],
    [_object([0, 2], 1, 1), _object([1, 3], 6, 1)],
    [_object([0], 1, 1), _object([1, 2], 5, 2), _object([3], 1, 4)],
]


@pytest.mark.parametrize("layout", LAYOUTS)
def test_cosmetic_changes_do_not_establish_three_presentations(layout):
    bundle = _bundle([deepcopy(layout) for _ in range(3)])
    for i, deck in enumerate(bundle["variants"].values()):
        for obj in deck["slides"][1]["objects"]:
            obj.update(color=f"00000{i}", font_sizes_pt=[16 + i], background=f"brand-{i}")
    report = assess_presentation_diversity(bundle)
    assert report["status"] == "failed"
    assert report["organization_status"] == report["composition_status"] == "failed"
    assert all(pair["source_coverage_complete"] for pair in report["pairs"])


def test_different_organization_and_composition_are_evidence_not_uncalibrated_success():
    report = assess_presentation_diversity(_bundle(LAYOUTS))
    assert report["status"] == "inconclusive"
    assert all(
        pair["organization"] == "different_literal_grouping_or_order" for pair in report["pairs"]
    )
    assert all(pair["composition"] == "different_content_geometry" for pair in report["pairs"])
    assert all(pair["slides"][0]["shared_cover_allowed"] for pair in report["pairs"])


def test_reordering_facts_in_the_same_layout_does_not_change_composition():
    layouts = [LAYOUTS[0], [_object([2, 3], 1, 1), _object([0, 1], 1, 3)], LAYOUTS[2]]
    pair = assess_presentation_diversity(_bundle(layouts))["pairs"][0]
    assert pair["organization"] == "different_literal_grouping_or_order"
    assert pair["composition_status"] == "failed"


def test_shared_table_does_not_override_distinct_body_evidence():
    bundle = _bundle(LAYOUTS)
    bundle["reference"]["points"].append({"id": "table-fact", "quote": "North 9", "required": True})
    for deck in bundle["variants"].values():
        table = _object([], 1, 1)
        table.update(table_rows=[["Group", "Count"], ["North", "9"]], text="Group Count North 9")
        deck["slides"].append({"number": 3, "text": table["text"], "objects": [table]})
    result = assess_presentation_diversity(bundle)
    assert result["status"] == "inconclusive"
    assert all(pair["slides"][2]["shared_tables_allowed"] == 1 for pair in result["pairs"])
    assert all(pair["source_coverage_complete"] for pair in result["pairs"])


def test_shared_cover_fact_is_exempt_but_body_duplicates_still_fail():
    bundle = _bundle([deepcopy(LAYOUTS[0]) for _ in range(3)])
    bundle["reference"]["points"].append(
        {"id": "cover-fact", "quote": "The trial is conditional.", "required": True}
    )
    for deck in bundle["variants"].values():
        obj = _object([], 1, 1)
        obj["text"] = "The trial is conditional."
        deck["slides"][0].update(text=obj["text"], objects=[obj])
    assert assess_presentation_diversity(bundle)["status"] == "failed"


def test_source_limited_and_paraphrased_inputs_cannot_get_an_automatic_pass():
    bundle = _bundle(LAYOUTS)
    bundle["reference"]["points"] = bundle["reference"]["points"][:1]
    assert assess_presentation_diversity(bundle)["status"] == "inconclusive"
    bundle = _bundle([deepcopy(LAYOUTS[0]) for _ in range(3)])
    for deck in bundle["variants"].values():
        deck["slides"][1]["objects"][0]["text"] = "A permissible paraphrase may retain the meaning."
    assert assess_presentation_diversity(bundle)["status"] == "inconclusive"


def test_uncertain_group_geometry_is_not_a_confirmed_cosmetic_duplicate():
    bundle = _bundle([deepcopy(LAYOUTS[0]) for _ in range(3)])
    for deck in bundle["variants"].values():
        deck["slides"][1]["objects"][0]["visibility"] = "uncertain_group_geometry"
    assert assess_presentation_diversity(bundle)["status"] == "inconclusive"


def test_same_topology_with_distinct_grouping_and_bounds_is_not_proven_duplicate():
    layouts = [
        [_object([0, 1], 1, 1), _object([2, 3], 1, 3)],
        [_object([0, 2], 3, 1, width=6), _object([1, 3], 3, 4, width=6)],
        [_object([0, 3], 2, 1, width=8), _object([1, 2], 2, 5, width=8)],
    ]
    report = assess_presentation_diversity(_bundle(layouts))
    assert report["status"] == "inconclusive"
    assert all(pair["composition_status"] == "inconclusive" for pair in report["pairs"])


def test_unanchored_explanations_are_not_dropped_from_comparison():
    bundle = _bundle([deepcopy(LAYOUTS[0]) for _ in range(3)])
    for index, deck in enumerate(bundle["variants"].values()):
        extra = _object([], 2 + index, 5)
        extra["text"] = f"An additional source explanation {index}."
        deck["slides"][1]["objects"].append(extra)
    assert assess_presentation_diversity(bundle)["status"] == "inconclusive"


def test_different_graphical_first_slides_are_not_assumed_shared_covers():
    bundle = _bundle([deepcopy(LAYOUTS[0]) for _ in range(3)])
    for index, deck in enumerate(bundle["variants"].values()):
        picture = _object([], 1, 1)
        picture.update(image={"pixels_sha256": f"different-source-image-{index}"})
        deck["slides"][0].update(text="", objects=[picture])
    report = assess_presentation_diversity(bundle)
    assert report["status"] == "inconclusive"
    assert not any(pair["slides"][0].get("shared_cover_allowed") for pair in report["pairs"])
