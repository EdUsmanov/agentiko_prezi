"""Reserve structural slides BEFORE content allocation, preserving source order."""

from .sections import section_groups
from .models import SlidePlan


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


def prepare_storyboard(package):
    facts = package.content.facts
    target = min(package.constraints.slides, len(facts))
    patterns = package.template.patterns
    covers = [p for p in patterns if p.reusable and p.role == "cover" and p.title_zone]
    dividers = [p for p in patterns if p.reusable and p.role == "divider" and p.title_zone]
    title_only = [
        p
        for p in patterns
        if p.reusable
        and p.purpose == "unknown"
        and p.title_zone
        and not p.body_zones
        and not p.image_zones
    ]
    if not dividers and (covers or title_only):
        # Explicitly derived chapter layout, not a false claim about the source.
        originals = (
            sorted(title_only, key=lambda p: p.title_zone.w * p.title_zone.h, reverse=True) + covers
        )[:3]
        for original in originals:
            p = original.model_copy(deep=True)
            p.id = "derived-divider-" + p.id
            p.role = p.purpose = "divider"
            p.body_zones = []
            p.text_zones = [p.title_zone]
            patterns.append(p)
            dividers.append(p)
        package.analysis["derived_divider"] = {
            "source_patterns": [p.id for p in originals],
            "method": "title_only_source_layout",
            "authored_divider_found": False,
        }
    groups = section_groups(package)
    if not groups:
        # A failed/absent chapter induction must not send table-heavy content
        # back to a splitter that can put several tables on one slide.
        groups = [{"title": package.content.title, "fact_ids": [f.id for f in facts]}]
    elif [fid for group in groups for fid in group["fact_ids"]] != [f.id for f in facts]:
        # Explicit source sections may omit unheaded introductory paragraphs.
        groups = [{"title": package.content.title, "fact_ids": [f.id for f in facts]}]
    from .archetypes import align_inferred_groups

    groups = align_inferred_groups(groups, package)
    # Do not manufacture a divider for every paragraph in deeply nested Markdown.
    if len(groups) > 4:
        group_count = min(3, max(2, target // 4))
        grouped = []
        for i in range(group_count):
            part = groups[i * len(groups) // group_count : (i + 1) * len(groups) // group_count]
            grouped.append(
                {"title": part[0]["title"], "fact_ids": [f for g in part for f in g["fact_ids"]]}
            )
        groups = grouped
    package.analysis["section_groups"] = groups
    by_id = {f.id: f for f in facts}
    tables = {t.id for t in package.content.tables}
    cover = bool(covers) and target >= 4
    chunks = []
    for gi, group in enumerate(groups):
        for fid in group["fact_ids"]:
            fact = by_id[fid]
            if chunks and chunks[-1]["group"] == gi and chunks[-1]["section"] == fact.section:
                chunk = chunks[-1]
                if not (
                    fact.source in tables and any(by_id[f].source in tables for f in chunk["ids"])
                ):
                    chunk["ids"].append(fid)
                    continue
            chunks.append({"group": gi, "section": fact.section, "ids": [fid]})
    from .archetypes import unit_chunks

    semantic_chunks = unit_chunks(package, groups)
    if semantic_chunks:
        chunks = semantic_chunks
    boundaries = list(dict.fromkeys(c["group"] for c in chunks))[1:]
    boundaries = boundaries[: max(1, target // 5)] if dividers and target >= 4 else []
    body_count = target - bool(cover) - len(boundaries)
    while len(chunks) > body_count:
        choices = [
            i
            for i in range(len(chunks) - 1)
            if chunks[i]["group"] == chunks[i + 1]["group"]
            and all(c.get("purpose", "content") == "content" for c in chunks[i : i + 2])
            and sum(by_id[f].source in tables for c in chunks[i : i + 2] for f in c["ids"]) <= 1
        ]
        if not choices:
            # Preserve sections/tables, finish analysis, disclose a larger draft.
            needed = len(chunks) + int(cover) + len(boundaries)
            message = (
                f"Запрошено {package.constraints.slides} слайдов на вариант. "
                f"Для сохранения смысловых блоков и раздельного размещения таблиц нужно {needed}. "
            )
            available = needed <= 30
            message += (
                f"Подготовлен план на {needed} слайдов; исходное содержание сохранено. "
                "Перед генерацией проверьте новое количество."
                if available
                else "Анализ сохранён, но генератор поддерживает не более 30 слайдов на вариант. "
                "Сократите материал или разделите его на несколько презентаций."
            )
            package.analysis["slide_budget"] = {
                "status": "adjusted" if available else "needs_input",
                "requested": package.constraints.slides,
                "count_mode": package.constraints.count_mode,
                "planned": needed if available else None,
                "required": needed,
                "cover": int(cover),
                "dividers": len(boundaries),
                "content_slides": len(chunks),
                "message": message,
            }
            package.analysis.setdefault("warnings", []).append(message)
            if not available:
                return
            target = needed
            body_count = len(chunks)
            break
        i = min(
            choices,
            key=lambda j: sum(len(by_id[f].text) for c in chunks[j : j + 2] for f in c["ids"]),
        )
        chunks[i]["ids"] += chunks[i + 1]["ids"]
        chunks[i]["section"] = (
            by_id[chunks[i]["ids"][0]].section or groups[chunks[i]["group"]]["title"]
        )
        chunks.pop(i + 1)
    while len(chunks) < body_count:
        choices = [
            i
            for i, c in enumerate(chunks)
            if len(c["ids"]) > 1 and c.get("purpose", "content") == "content"
        ]
        if not choices:
            if semantic_chunks:
                needed = len(chunks) + int(cover) + len(boundaries)
                message = (
                    f"Запрошено {package.constraints.slides} слайдов на вариант. "
                    f"Подготовлен план на {needed}: процессы и сравнения не дробятся ради числа слайдов. "
                    "Перед генерацией проверьте новое количество."
                )
                package.analysis["slide_budget"] = {
                    "status": "adjusted",
                    "requested": package.constraints.slides,
                    "count_mode": package.constraints.count_mode,
                    "planned": needed,
                    "required": needed,
                    "cover": int(cover),
                    "dividers": len(boundaries),
                    "content_slides": len(chunks),
                    "message": message,
                }
                package.analysis.setdefault("warnings", []).append(message)
                break
            raise ValueError("Недостаточно материала для заданного числа слайдов без повторов")
        i = max(choices, key=lambda j: sum(len(by_id[f].text) for f in chunks[j]["ids"]))
        c = chunks[i]
        midpoint = len(c["ids"]) // 2
        chunks[i : i + 1] = [{**c, "ids": c["ids"][:midpoint]}, {**c, "ids": c["ids"][midpoint:]}]
    outline = []
    if cover:
        outline.append(
            SlidePlan(
                title=package.content.title[:240], fact_ids=[], purpose="cover", role="context"
            )
        )
    inserted = set()
    for chunk in chunks:
        gi = chunk["group"]
        if gi in boundaries and gi not in inserted:
            outline.append(
                SlidePlan(
                    title=groups[gi]["title"],
                    fact_ids=[],
                    layout="divider",
                    purpose="divider",
                    role="context",
                )
            )
            inserted.add(gi)
        tid = next((by_id[f].source for f in chunk["ids"] if by_id[f].source in tables), None)
        from collections import Counter

        topics = Counter(
            by_id[f].section
            for f in chunk["ids"]
            if by_id[f].section and by_id[f].section != package.content.title
        )
        title = topics.most_common(1)[0][0] if topics else chunk["section"] or groups[gi]["title"]
        purpose = chunk.get("purpose", "content")
        layout = (
            "table"
            if tid
            else "process"
            if purpose == "process"
            else "columns"
            if purpose == "comparison"
            else "split"
        )
        outline.append(
            SlidePlan(
                title=title[:240],
                fact_ids=chunk["ids"],
                table_id=tid,
                layout=layout,
                purpose=purpose,
                role="evidence" if tid else "insight",
            )
        )
    outline = fit_storyboard(package, outline)
    package.analysis["storyboard"] = [s.model_dump() for s in outline]
    package.analysis["section_dividers"] = {
        "added": 3 * len(inserted),
        "per_variant": {key: len(inserted) for key in ("executive", "analytical", "story")},
        "reason": "reserved_before_content_allocation",
    }


def fit_storyboard(package, outline):
    """Capacity is a preparation constraint, not a warning after publication.

    Split at source fact boundaries, preserving order and complete table cells.
    Any slide-count change remains an explicit confirmation in the existing UI.
    """
    from .composer import compose
    from .audit import audit_scenes, repair_scenes
    from .content import slide_heading

    by_id = {f.id: f for f in package.content.facts}
    table_ids = {t.id for t in package.content.tables}
    probe = package.model_copy(
        update={"analysis": {}, "constraints": package.constraints.model_copy(update={"slides": 1})}
    )
    result = []
    pending = list(outline)
    geometry = {
        "container_overflow",
        "out_of_bounds",
        "text_overflow",
        "table_overflow",
        "chart_overflow",
        "overlap",
    }
    while pending:
        slide = pending.pop(0)
        slide.title = slide_heading(slide.title) or slide.title
        try:
            scene = compose(slide, package, len(result), "executive")
            repair_scenes([scene], package)
            bad = any(
                f.code in geometry and f.severity == "error" for f in audit_scenes([scene], probe)
            )
        except ValueError:
            bad = True
        if not bad or len(slide.fact_ids) < 2 or slide.layout == "divider":
            result.append(slide)
            continue
        if len(result) + len(pending) + 2 > 30:
            raise ValueError(
                "Материал не помещается в 30 читаемых слайдов; сократите текст или разделите презентацию."
            )
        ids = slide.fact_ids
        # Prefer source paragraph boundaries near the middle. Do not split a
        # sentence or alter an atomic table to make the geometry look valid.
        choices = [i for i in range(1, len(ids)) if by_id[ids[i - 1]].line != by_id[ids[i]].line]
        middle = min(choices or range(1, len(ids)), key=lambda i: abs(i - len(ids) / 2))
        parts = []
        for index, part in enumerate((ids[:middle], ids[middle:])):
            tid = next((by_id[f].source for f in part if by_id[f].source in table_ids), None)
            parts.append(
                slide.model_copy(
                    deep=True,
                    update={
                        "fact_ids": part,
                        "table_id": tid,
                        "pattern_id": None,
                        "layout": slide.layout if tid else "columns",
                        "purpose": "content",
                        "title": slide.title if index == 0 else slide.title + " (продолжение)",
                    },
                )
            )
        pending = parts + pending
    if len(result) != len(outline):
        message = (
            f"Запрошено {package.constraints.slides} слайдов. Для читаемого размещения без потери фактов "
            f"подготовлено {len(result)}. Переполненные блоки разделены по границам исходного текста; подтвердите количество."
        )
        package.analysis["slide_budget"] = {
            "status": "adjusted",
            "requested": package.constraints.slides,
            "count_mode": package.constraints.count_mode,
            "planned": len(result),
            "required": len(result),
            "message": message,
        }
        package.analysis.setdefault("warnings", []).append(message)
    return result
