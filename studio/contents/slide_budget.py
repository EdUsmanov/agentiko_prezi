"""Validate a persisted slide count without invoking planning or composition."""


def count_was_adjusted(package):
    """A proposed semantic plan is not necessarily a change to the requested count."""
    from studio.contents.parsing import SLIDE_RANGES

    budget = package.control.slide_budget
    if not budget or budget.status != "adjusted" or budget.planned is None:
        return False
    constraints = package.constraints
    if constraints.count_mode == "default" and constraints.size_preset:
        minimum, maximum = SLIDE_RANGES[constraints.size_preset]
        return not minimum <= budget.planned <= maximum
    if constraints.count_mode == "maximum":
        return budget.planned > constraints.slides
    if constraints.count_mode == "minimum":
        return budget.planned < constraints.slides
    return budget.planned != constraints.slides


def planned_slide_count(package):
    """Use only a server-built, persisted budget adjustment; keep user constraints."""
    budget = package.control.slide_budget
    if budget and budget.status == "adjusted":
        count = budget.planned
        if (
            type(count) is not int
            or not 1 <= count <= 30
            or budget.requested != package.constraints.slides
            or len(package.analysis.get("storyboard", [])) != count
            or package.constraints.count_mode == "minimum"
            and count < package.constraints.slides
        ):
            raise ValueError("Некорректное согласование количества слайдов")
        return count
    return min(package.constraints.slides, len(package.content.facts))
