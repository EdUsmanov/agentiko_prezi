"""Validate reviewer findings against the actual authored plan."""

from .models import ContextualAudit


def grounded_review(raw, plans):
    parsed = ContextualAudit.model_validate(raw)
    slides = {(v.key, i): s for v in plans.variants for i, s in enumerate(v.slides, 1)}
    for finding in parsed.findings:
        slide = slides.get((finding.variant, finding.slide))
        if (
            slide is None
            or finding.title_quote != slide.title
            or not set(finding.fact_ids) <= set(slide.fact_ids)
        ):
            raise ValueError("Замечание critic не привязано к фактическому слайду")
    return parsed.model_dump()
