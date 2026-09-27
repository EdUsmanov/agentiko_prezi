"""Read and validate ordered source chapter membership."""

from .content import slide_heading


def source_sections(package):
    groups = []
    for fact in package.content.facts:
        if not groups or groups[-1]["section"] != fact.section:
            groups.append({"section": fact.section, "fact_ids": []})
        groups[-1]["fact_ids"].append(fact.id)
    return groups


def section_groups(package):
    groups = package.analysis.get("section_groups")
    if groups:
        expected = [f.id for f in package.content.facts]
        if [f for g in groups for f in g["fact_ids"]] != expected:
            raise ValueError("Смысловые разделы не покрывают исходный материал по порядку")
        return groups
    groups = source_sections(package)
    # Slide numbers are not chapters. Do not manufacture meaningless separators.
    if any(slide_heading(g["section"]) is not None for g in groups):
        return []
    return [{"title": g["section"], "fact_ids": g["fact_ids"]} for g in groups if g["section"]]


def divider_members(package):
    result = {}
    for group in section_groups(package):
        result.setdefault(group["title"], set()).update(group["fact_ids"])
    return result
