"""Validate a persisted slide count without invoking planning or composition."""


def planned_slide_count(package):
    """Use only a server-built, persisted budget adjustment; keep user constraints."""
    budget = package.analysis.get("slide_budget", {})
    if budget.get("status") == "adjusted":
        count = budget.get("planned")
        if (
            type(count) is not int
            or not 1 <= count <= 30
            or budget.get("requested") != package.constraints.slides
            or len(package.analysis.get("storyboard", [])) != count
            or package.constraints.count_mode == "minimum"
            and count < package.constraints.slides
        ):
            raise ValueError("Некорректное согласование количества слайдов")
        return count
    return min(package.constraints.slides, len(package.content.facts))
