import pytest
from studio.contracts import compatible
from studio.models import Pattern, SlidePlan


@pytest.mark.parametrize("layout,purpose", [("chart", "trend"), ("table", "metrics")])
def test_quantitative_evidence_can_use_claim_evidence_layout(layout, purpose):
    pattern = Pattern(
        id="evidence",
        source_slide=18,
        source_layout="Evidence",
        text_zones=[],
        role="statement",
        purpose="claim_evidence",
    )
    slide = SlidePlan(
        title="Results", fact_ids=["f1"], table_id="t1", layout=layout, purpose=purpose
    )
    assert compatible(pattern, slide, 1)
    pattern.reusable = False
    assert not compatible(pattern, slide, 1)


@pytest.mark.parametrize(
    "purpose", ["cover", "divider", "service", "reference", "comparison", "process"]
)
def test_quantitative_evidence_does_not_override_other_semantic_contracts(purpose):
    pattern = Pattern(
        id="other",
        source_slide=1,
        source_layout="Other",
        text_zones=[],
        role="statement",
        purpose=purpose,
    )
    slide = SlidePlan(
        title="Results", fact_ids=["f1"], table_id="t1", layout="chart", purpose="trend"
    )
    assert not compatible(pattern, slide, 1)


def test_numerical_exception_does_not_apply_to_ordinary_prose():
    pattern = Pattern(
        id="evidence",
        source_slide=18,
        source_layout="Evidence",
        text_zones=[],
        role="statement",
        purpose="claim_evidence",
    )
    slide = SlidePlan(title="Context", fact_ids=["f1"], layout="statement", purpose="content")
    assert not compatible(pattern, slide, 1)
