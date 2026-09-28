"""Semantic chapters and budgeted dividers, without losing source evidence."""

from .section_metadata import (
    source_sections as source_sections,
    section_groups as section_groups,
    divider_members as divider_members,
)

from collections import Counter
from pydantic import Field
from .models import SlidePlan, StrictModel
from .security import INJECTION


class Chapter(StrictModel):
    title: str = Field(min_length=1, max_length=100)
    sections: list[int] = Field(min_length=1)


class Chapters(StrictModel):
    chapters: list[Chapter] = Field(min_length=2, max_length=4)


async def prepare_sections(package, gateway):
    """Induce chapters for plain text and explicit outlines before planning."""
    package.analysis.pop("section_groups", None)
    groups = source_sections(package)
    if len(groups) == 1 and not groups[0]["section"]:
        groups = [{"section": "", "fact_ids": [f.id]} for f in package.content.facts]
    if len(groups) < 4 or gateway.settings.mode != "api":
        package.analysis["section_grouping"] = {
            "status": "not_run",
            "reason": "insufficient_sections_or_no_model",
        }
        return
    facts = {f.id: f for f in package.content.facts}
    try:
        schema = Chapters.model_json_schema()
        max_chapters = min(4, max(2, package.constraints.slides // 5))
        schema["properties"]["chapters"]["maxItems"] = max_chapters
        schema["$defs"]["Chapter"]["properties"]["sections"]["items"] = {
            "type": "integer",
            "enum": list(range(len(groups))),
        }
        payload = {
            "total_slide_budget": package.constraints.slides,
            "max_chapters": max_chapters,
            "sections": [
                {
                    "index": i,
                    "heading": g["section"],
                    "text": "\n".join(facts[f].text for f in g["fact_ids"])[:3000],
                }
                for i, g in enumerate(groups)
            ],
        }
        parsed = Chapters.model_validate(
            await gateway.json_request("sections", payload, timeout=120, schema=schema)
        )
        if len(parsed.chapters) > max_chapters:
            payload["validation_error"] = (
                f"Return at most {max_chapters} chapters within the slide budget; merge adjacent related chapters. Preserve all ordered section indices."
            )
            parsed = Chapters.model_validate(
                await gateway.json_request("sections", payload, timeout=120, schema=schema)
            )
        if len(parsed.chapters) > max_chapters:
            raise ValueError("Too many chapters for the slide budget")
        flat = [i for chapter in parsed.chapters for i in chapter.sections]
        if flat != list(range(len(groups))):
            raise ValueError("Chapters must partition the ordered input")
        result = []
        for chapter in parsed.chapters:
            ids = [f for i in chapter.sections for f in groups[i]["fact_ids"]]
            evidence = "\n".join(facts[f].text + "\n" + facts[f].section for f in ids)
            from .editorial_domain import nums

            if INJECTION.search(chapter.title) or not set(nums(chapter.title)) <= set(
                nums(evidence)
            ):
                raise ValueError("Unsupported chapter title")
            result.append({"title": chapter.title, "fact_ids": ids})
        if len({g["title"] for g in result}) != len(result):
            raise ValueError("Duplicate chapter titles")
        package.analysis["section_groups"] = result
        package.analysis["section_grouping"] = {
            "status": "completed",
            "method": "semantic_ordered_partition",
        }
    except Exception as exc:
        package.analysis["section_grouping"] = {
            "status": "failed",
            "error_type": type(exc).__name__,
        }
        package.analysis.setdefault("warnings", []).append(
            "Не удалось выделить смысловые разделы моделью; используется явная структура исходного текста, если она есть."
        )


def add_dividers(plans, package):
    if not any(p.role == "divider" and p.title_zone for p in package.template.patterns):
        package.analysis["section_dividers"] = {"added": 0, "reason": "no_template_divider"}
        return plans
    groups = section_groups(package)
    facts = {f.id: f for f in package.content.facts}
    group_of = {f: i for i, g in enumerate(groups) for f in g["fact_ids"]}

    def section(slide):
        indices = {group_of.get(f) for f in slide.fact_ids}
        return next(iter(indices)) if len(indices) == 1 and None not in indices else None

    from .composer import compose_variant
    from .audit import audit_scenes, repair_scenes

    def errors(variant):
        scenes = compose_variant(variant, package)
        repair_scenes(scenes, package)
        return Counter(
            (f.code, tuple(scenes[f.slide - 1].source_ids) if f.slide else ())
            for f in audit_scenes(scenes, package)
            if f.severity == "error"
        )

    result = plans.model_copy(deep=True)
    added = 0
    per_variant = {}
    for variant in result.variants:
        inserted = 0
        order = {f.id: i for i, f in enumerate(package.content.facts)}
        if package.analysis.get("section_groups"):
            for slide in variant.slides:
                slide.fact_ids.sort(key=order.__getitem__)
        if any(s.layout == "divider" for s in variant.slides):
            continue
        baseline = errors(variant)
        boundaries = list(
            dict.fromkeys(
                section(b)
                for a, b in zip(variant.slides, variant.slides[1:])
                if section(a) is not None and section(b) is not None and section(a) != section(b)
            )
        )
        for target in boundaries:
            if inserted >= max(1, len(variant.slides) // 4):
                break
            pairs = [
                i
                for i, (a, b) in enumerate(zip(variant.slides, variant.slides[1:]))
                if i > 0
                and a.layout != "divider"
                and b.layout != "divider"
                and section(a) is not None
                and section(a) == section(b)
                and not a.table_id
                and not b.table_id
            ]
            pairs.sort(
                key=lambda i: sum(
                    len(facts[f].text) for s in variant.slides[i : i + 2] for f in s.fact_ids
                )
            )
            for i in pairs:
                trial = variant.model_copy(deep=True)
                a, b = trial.slides[i : i + 2]
                a.fact_ids = list(dict.fromkeys(a.fact_ids + b.fact_ids))
                a.title = groups[section(a)]["title"]
                a.pattern_id = None
                trial.slides.pop(i + 1)
                at = next(
                    (
                        j
                        for j, s in enumerate(trial.slides)
                        if j > 0 and section(s) == target and section(trial.slides[j - 1]) != target
                    ),
                    None,
                )
                if at is None:
                    continue
                trial.slides.insert(
                    at,
                    SlidePlan(
                        title=groups[target]["title"], fact_ids=[], layout="divider", role="context"
                    ),
                )
                if errors(trial) - baseline:
                    continue
                variant.slides = trial.slides
                baseline = errors(variant)
                inserted += 1
                added += 1
                break
        per_variant[variant.key] = inserted
    package.analysis["section_dividers"] = {
        "added": added,
        "per_variant": per_variant,
        "reason": "within_slide_budget" if added else "no_safe_section_boundary_or_space",
    }
    if groups and len(groups) > 1 and any(n == 0 for n in per_variant.values()):
        package.analysis.setdefault("warnings", []).append(
            "Для части вариантов разделители не поместились в лимит без переполнения. Увеличьте число слайдов или сократите материал."
        )
    return result
