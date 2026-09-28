"""Route scene defects by object and cause, never by diagnostic text."""

from .models import Finding, SlideScene
from .repair_errors import RepairIssue

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
        # A table's readability failure is a data-layout problem, even when
        # neighbouring prose is long. Never shorten data or labels to hide it.
        text_repair = (
            finding.code in {"text_overflow", "readability"}
            and element is not None
            and element.kind == "text"
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


def scene_fit_feedback(
    scene: SlideScene, findings: list[Finding], slide: int, editable_fact_ids: set[str]
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
                "target_max_characters": max(
                    12, int(element.box.w / 9) * max(1, int(element.box.h / 22))
                ),
            }
            for index, element in enumerate(scene.elements)
            if index in text_indices
        ],
    }
