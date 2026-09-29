"""Route scene defects by object and cause, never by diagnostic text."""

from studio.models import Finding, SlideScene
from studio.checks.repair_errors import RepairIssue

FIT_CODES = frozenset(
    {
        "background_conflict",
        "container_overflow",
        "out_of_bounds",
        "text_overflow",
        "table_overflow",
        "chart_overflow",
        "overlap",
        "readability",
        "visualization_intent",
    }
)


def scene_repair_issues(
    scene: SlideScene, findings: list[Finding], slide: int, editable_fact_ids: set[str]
) -> list[RepairIssue]:
    issues = []
    seen = set()
    for finding in findings:
        if finding.code not in FIT_CODES or (finding.code, finding.element) in seen:
            continue
        seen.add((finding.code, finding.element))
        element = (
            scene.elements[finding.element]
            if finding.element is not None and 0 <= finding.element < len(scene.elements)
            else None
        )
        editable_text = (
            element is not None
            and element.kind == "text"
            and (
                element.role == "title"
                or (element.source_ids and set(element.source_ids) <= editable_fact_ids)
            )
        )
        # Shortening cannot make text readable if its box cannot hold even one
        # line at the role's readability floor and the renderer's line spacing.
        text_floor_cannot_fit = (
            finding.code in {"text_overflow", "readability"}
            and editable_text
            and element.box.h < (18 if element.role == "title" else 16) * 1.25
        )
        # A table's readability failure is a data-layout problem, even when
        # neighbouring prose is long. Never shorten data or labels to hide it.
        text_repair = (
            finding.code in {"text_overflow", "readability"}
            and element is not None
            and element.kind == "text"
            and not text_floor_cannot_fit
            and (
                element.role == "title"
                or (element.source_ids and set(element.source_ids) <= editable_fact_ids)
            )
        )
        issues.append(
            RepairIssue(
                code=finding.code,
                message=finding.message,
                slide=slide,
                element=finding.element,
                action="shorten_text" if text_repair else "adapt_layout",
            )
        )
    return issues


def shortening_target(element, profile=None):
    """A measured length hint, never a substitute for fit and meaning checks."""
    size = 18 if element.role == "title" else 16
    width = max(1, element.box.w - (size * 1.4 if element.bullet else 0))
    width *= 0.94 if element.bold or element.bold_prefix else 1
    lines = max(1, int(element.box.h / (size * 1.25)))
    # A failed field must receive a smaller target, even when an approximate
    # capacity calculation says its current character count fits already.
    upper = max(1, len(element.text) - 1)
    if profile is None:
        return min(upper, max(1, int(width / 9) * lines))
    from studio.templates.fonts import element_font, text_width, wrap_text

    font_file = element_font(profile, element)[1]
    low, high = 0, upper
    while low < high:
        count = (low + high + 1) // 2
        wrapped = wrap_text(element.text[:count], font_file, size, width)
        if len(wrapped) <= lines and all(
            text_width(line, font_file, size) <= width for line in wrapped
        ):
            low = count
        else:
            high = count - 1
    return max(1, len(element.text[:low].rstrip()))


def scene_fit_feedback(
    scene: SlideScene,
    findings: list[Finding],
    slide: int,
    editable_fact_ids: set[str],
    *,
    profile=None,
) -> dict:
    issues = scene_repair_issues(scene, findings, slide, editable_fact_ids)
    text_indices = {issue.element for issue in issues if issue.action == "shorten_text"}
    return {
        "slide": slide,
        "pattern_id": scene.pattern_id,
        "findings": [
            finding.model_copy(update={"slide": slide}).model_dump()
            for finding in findings
            if finding.code in FIT_CODES
        ],
        "repair_issues": [issue.model_dump() for issue in issues],
        "fields": [
            {
                "element": index,
                "role": element.role,
                "text": element.text,
                "fact_ids": element.source_ids,
                "current_font_size": element.size,
                "minimum_font_size": 18 if element.role == "title" else 16,
                "width": round(element.box.w),
                "height": round(element.box.h),
                "target_max_characters": shortening_target(element, profile),
            }
            for index, element in enumerate(scene.elements)
            if index in text_indices
        ],
    }
