from types import SimpleNamespace
import pytest
from studio.models import Pattern, Box, Fact, ContentModel, SlidePlan
from studio.contracts import compatible, candidates, apply_meanings
from studio.semantic_bindings import bind_groups


def pattern(kind="cards"):
    boxes = [Box(x=20 + i * 200, y=100, w=180, h=200) for i in range(3)]
    return Pattern(
        id="p",
        source_slide=1,
        source_layout="test",
        role="columns",
        purpose="content",
        title_zone=Box(x=20, y=20, w=600, h=60),
        text_zones=boxes,
        body_zones=boxes,
        graphic_kind=kind,
        graphic_order_verified=True,
        fields=[{"role": "body", "index": i, "shape_id": i + 10} for i in range(3)],
    )


@pytest.mark.parametrize(
    "kind,purpose,wrong",
    [
        ("sequence", "process", "content"),
        ("comparison", "comparison", "process"),
        ("hierarchy", "structure", "timeline"),
        ("radial", "composition", "process"),
        ("pyramid", "structure", "comparison"),
        ("matrix", "comparison", "content"),
    ],
)
def test_graphic_relationship_cannot_be_reused_for_unrelated_content(kind, purpose, wrong):
    p = pattern(kind)
    assert compatible(p, SlidePlan(title="Title", fact_ids=["f"], purpose=purpose))
    assert not compatible(p, SlidePlan(title="Title", fact_ids=["f"], purpose=wrong))


def test_generalized_editorial_bindings_support_recommendation_cards():
    facts = [Fact(id=f"f{i}", text=f"Действие {i}") for i in range(3)]
    slide = SlidePlan(title="Действия", fact_ids=[f.id for f in facts], purpose="recommendations")
    package = SimpleNamespace(
        content=ContentModel(title="Тема", facts=facts),
        analysis={
            "editorial": {
                "bindings": [
                    {
                        "purpose": "recommendations",
                        "fact_ids": slide.fact_ids,
                        "groups": [
                            {"label": f"Шаг {i}", "fact_ids": [f.id]} for i, f in enumerate(facts)
                        ],
                    }
                ]
            }
        },
    )
    bound = bind_groups(slide, package, pattern())
    assert bound["status"] == "specialized"
    assert [g["shape_id"] for g in bound["groups"]] == [10, 11, 12]


def test_verified_generic_vectors_survive_semantic_application():
    p = pattern("none")
    apply_meanings(
        SimpleNamespace(patterns=[p]),
        {
            "patterns": [
                {
                    "pattern_id": "p",
                    "purpose": "content",
                    "reusable": True,
                    "graphic_kind": "cards",
                    "graphic_shape_ids": [40, 41, 42],
                    "graphic_flow_confirmed": True,
                    "body_order": [12, 10, 11],
                }
            ]
        },
    )
    assert p.graphic_kind == "cards" and p.graphic_shape_ids == [40, 41, 42]
    assert [b.x for b in p.body_zones] == [420, 20, 220]


def test_ambiguous_content_cannot_fill_verified_graphics():
    p = pattern()
    content = ContentModel(title="Тема", facts=[Fact(id="f", text="Один абзац")])
    package = SimpleNamespace(template=SimpleNamespace(patterns=[p]), content=content, analysis={})
    assert candidates(package, SlidePlan(title="Title", fact_ids=["f"], purpose="content")) == []


def test_hierarchy_edges_must_match_new_content_relationships():
    from studio.semantic_bindings import structure_matches

    p = pattern("hierarchy")
    p.graphic_edges = [(10, 11), (10, 12)]
    groups = [
        {"label": "Root", "parent": None},
        {"label": "A", "parent": "Root"},
        {"label": "B", "parent": "Root"},
    ]
    assert structure_matches(p, groups)
    groups[2]["parent"] = "A"
    assert not structure_matches(p, groups)


def test_graphic_contract_is_enforced_even_when_preference_is_disabled():
    p = pattern()
    content = ContentModel(title="Тема", facts=[Fact(id="f", text="Один абзац")])
    package = SimpleNamespace(template=SimpleNamespace(patterns=[p]), content=content, analysis={})
    assert (
        candidates(
            package,
            SlidePlan(title="Title", fact_ids=["f"], purpose="content"),
            prefer_specialized=False,
        )
        == []
    )


@pytest.mark.parametrize("source,target", [("process", "timeline"), ("timeline", "process")])
def test_verified_sequence_accepts_both_dated_events_and_steps(source, target):
    p = pattern("sequence")
    p.purpose = source
    assert compatible(p, SlidePlan(title="История", fact_ids=["f"], purpose=target))
    assert not compatible(p, SlidePlan(title="Сравнение", fact_ids=["f"], purpose="comparison"))


def test_editorial_selection_rewrites_for_matching_graphics_instead_of_plain_fallback():
    p = pattern("sequence")
    p.purpose = "process"
    generic = p.model_copy(
        deep=True,
        update={
            "id": "generic",
            "source_slide": None,
            "graphic_kind": "none",
            "purpose": "content",
        },
    )
    facts = [Fact(id=f"f{i}", text="Длинное описание события. " * 30) for i in range(3)]
    slide = SlidePlan(title="История", fact_ids=[f.id for f in facts], purpose="timeline")
    package = SimpleNamespace(
        template=SimpleNamespace(patterns=[generic, p]),
        content=ContentModel(title="История", facts=facts),
        analysis={
            "editorial": {
                "bindings": [
                    {
                        "purpose": "timeline",
                        "fact_ids": slide.fact_ids,
                        "groups": [
                            {"label": str(i), "fact_ids": [f.id]} for i, f in enumerate(facts)
                        ],
                    }
                ]
            }
        },
    )
    assert [x.id for x in candidates(package, slide)] == ["p"]
    assert [x.id for x in candidates(package, slide, prefer_specialized=False)] == ["generic", "p"]
    package.analysis["editorial"]["bindings"][0]["groups"].pop()
    assert [x.id for x in candidates(package, slide, prefer_specialized=False)] == ["generic"]
