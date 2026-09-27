"""Grounded evidence-to-field contracts shared by rendering backends.

Never distribute comparison facts by list position: two identical prices can
belong to different products. Ambiguous relationships keep their full source
text in a general field rather than inventing a participant/value association.
"""

import re
from .contracts import body_and_title_sources


def inline_group_text(label, texts):
    """Keep all evidence, adding a shared-field label only when it is absent."""
    text = "\n".join(texts)
    label = label.strip().rstrip(".:")
    if not label or re.match(re.escape(label) + r"(?!\w)", text.lstrip(), re.I):
        return text
    return label + ". " + text


def canonicalize_storyboard(plans, package):
    """Three visual variants, one evidence allocation and narrative."""
    canonical = plans.variants[0].slides
    keys = ("title", "fact_ids", "purpose", "table_id", "role")
    for variant in plans.variants[1:]:
        if len(variant.slides) != len(canonical):
            raise ValueError("Варианты должны использовать один сценарий")
        for slide, source in zip(variant.slides, canonical):
            if slide.fact_ids != source.fact_ids:
                raise ValueError("Варианты расходятся в распределении подтверждённых фактов")
            for key in keys:
                value = getattr(source, key)
                setattr(slide, key, value[:] if isinstance(value, list) else value)
    package.analysis["canonical_storyboard"] = [
        {key: getattr(s, key) for key in keys} for s in canonical
    ]
    package.analysis["storyboard_policy"] = "one_verified_scenario; engines_are_layout_executors"


def content_groups(slide, package):
    facts, _ = body_and_title_sources(slide, package.content)
    ids = {f.id for f in facts}
    for binding in package.analysis.get("editorial", {}).get("bindings", []):
        if set(binding["fact_ids"]) == ids and binding["purpose"] == slide.purpose:
            by_id = {f.id: f for f in facts}
            groups = [
                {
                    "label": g["label"],
                    "facts": [by_id[f] for f in g["fact_ids"]],
                    "slots": [],
                    "parent": g.get("parent"),
                }
                for g in binding["groups"]
            ]
            if len(groups) >= 2 and {f.id for g in groups for f in g["facts"]} == ids:
                return {
                    "status": "specialized",
                    "reason": "reviewed_editorial_participants",
                    "groups": groups,
                }
    units = [
        u
        for u in package.analysis.get("archetypes", {}).get("units", [])
        if set(u["fact_ids"]) == ids and u["purpose"] == slide.purpose
    ]
    fallback = {
        "status": "general",
        "reason": "No unambiguous specialized binding",
        "groups": [{"label": "", "facts": facts, "slots": []}] if facts else [],
    }
    if len(units) != 1 or slide.table_id:
        return fallback
    unit = units[0]
    slots = unit.get("slots", [])
    if slide.purpose == "comparison":
        entities = list(dict.fromkeys(s["quote"] for s in slots if s["role"] == "entity"))
        if len(entities) < 2:
            return fallback
        groups = [{"label": name, "facts": [], "slots": []} for name in entities]
        for fact in facts:
            matches = {
                s["quote"] for s in slots if s["role"] == "entity" and s["fact_id"] == fact.id
            }
            # A section can establish ownership for several facts, but a mention
            # of two entities in one paragraph does not establish value ownership.
            matches.update(name for name in entities if name == fact.section)
            if len(matches) != 1:
                return fallback
            group = groups[entities.index(next(iter(matches)))]
            group["facts"].append(fact)
            group["slots"].extend(s for s in slots if s["fact_id"] == fact.id)
        if any(not g["facts"] for g in groups):
            return fallback
    elif slide.purpose in ("process", "timeline"):
        role = "step" if slide.purpose == "process" else "date"
        selected = [s for s in slots if s["role"] == role]
        # Splitting one sentence into invented step descriptions is forbidden.
        if [s["fact_id"] for s in selected] != [f.id for f in facts]:
            return fallback
        groups = [
            {"label": str(i + 1) if role == "step" else s["quote"], "facts": [fact], "slots": [s]}
            for i, (s, fact) in enumerate(zip(selected, facts))
        ]
    else:
        return fallback
    return {"status": "specialized", "reason": "", "groups": groups}


def structure_matches(pattern, groups):
    if pattern.graphic_kind not in ("hierarchy", "pyramid", "radial"):
        return True
    fields = {f["shape_id"]: f["index"] for f in pattern.fields if f["role"] == "body"}
    labels = {g["label"]: i for i, g in enumerate(groups)}
    try:
        expected = {(fields[a], fields[b]) for a, b in pattern.graphic_edges}
        supplied = {(labels[g["parent"]], i) for i, g in enumerate(groups) if g.get("parent")}
    except KeyError:
        return False
    return bool(expected) and expected == supplied


def bind_groups(slide, package, pattern):
    result = content_groups(slide, package)
    if result["status"] != "specialized":
        return result
    if (
        len(pattern.body_zones) < len(result["groups"])
        or pattern.graphic_kind != "none"
        and len(pattern.body_zones) != len(result["groups"])
        or not structure_matches(pattern, result["groups"])
    ):
        return {
            **result,
            "status": "general",
            "reason": "Template field count does not match semantic participants",
        }
    bindings = []
    for i, group in enumerate(result["groups"]):
        field = next((f for f in pattern.fields if f["role"] == "body" and f["index"] == i), None)
        bindings.append(
            {
                **group,
                "box": pattern.body_zones[i],
                "heading": pattern.heading_zones[i] if i < len(pattern.heading_zones) else None,
                "shape_id": field["shape_id"] if field else None,
            }
        )
    return {**result, "groups": bindings}


def binding_report(slide, package, pattern, visuals=None):
    if slide.table_id:
        table = next(t for t in package.content.tables if t.id == slide.table_id)
        if visuals is not None:
            shape_id = next(v["shape_id"] for v in visuals if v.get("table_id") == slide.table_id)
        else:
            body, _ = body_and_title_sources(slide, package.content)
            tables = {t.id for t in package.content.tables}
            prose = [f.text for f in body if f.source not in tables]
            _, field, _ = table_region(
                table,
                pattern,
                package.template,
                bool(prose),
                chart=slide.layout == "chart",
                body_texts=prose,
            )
            shape_id = field["shape_id"]
        return {
            "status": "specialized",
            "reason": "source_table_cell_mapping",
            "archetype": slide.purpose,
            "pattern_id": pattern.id,
            "fields": [
                {
                    "table_id": table.id,
                    "shape_id": shape_id,
                    "fact_ids": slide.fact_ids,
                    "columns": table.headers,
                    "row_count": len(table.rows),
                }
            ],
        }
    result = bind_groups(slide, package, pattern)
    return {
        "status": result["status"],
        "reason": result["reason"],
        "archetype": slide.purpose,
        "pattern_id": pattern.id,
        "fields": [
            {
                "label": g["label"],
                "fact_ids": [f.id for f in g["facts"]],
                "shape_id": g.get("shape_id"),
                "slots": g["slots"],
            }
            for g in result["groups"]
        ],
    }


def contract_overflow(contract, profile):
    """Estimate minimum readable text capacity before truncating layout choices."""
    from .fonts import role_font, wrap_text

    overflow = 0
    for field in contract["fields"].values():
        if not field["paragraphs"]:
            continue
        size = field.get("font_size") or (16 if field["role"] == "title" else 12)
        box = field["box"]
        font = role_font(profile, field["role"])[1]
        lines = sum(
            len(wrap_text(t, font, size, max(1, box["w"] * 0.96))) for t in field["paragraphs"]
        )
        overflow += max(0, lines * size * 1.25 - box["h"])
    return overflow + sum(v.get("capacity_overflow", 0) for v in contract["visuals"])


def fit_contract_text(contract, profile):
    """Bounded fitting before selection, never authorize unreadable overflow.

    Keep source sizes whenever possible; preserve the chosen size in the
    contract so export does not rediscover a different layout decision.
    """
    for field in contract["fields"].values():
        if not field["paragraphs"]:
            continue
        original = field.get("font_size") or (16 if field["role"] == "title" else 12)
        floor = min(original, 16 if field["role"] == "title" else 12)
        probe = {"fields": {"field": field}, "visuals": []}
        if contract_overflow(probe, profile) <= 0.01:
            continue
        for step in range(1, 21):
            field["font_size"] = max(floor, original * (1 - step * 0.035))
            if contract_overflow(probe, profile) <= 0.01:
                field["source_font_size"] = original
                break
        else:
            field["font_size"] = original


def table_capacity(table, box, profile, minimum_size=10):
    """Return the number of cells that cannot fit at the minimum readable size."""
    from .fonts import role_font, table_cell_fits

    if table.visualization == "metrics":
        from .models import Element
        from .metrics import metric_elements

        try:
            metric_elements(
                Element(
                    kind="table",
                    box=box,
                    rows=[table.headers] + table.rows,
                    font=profile.font,
                    color=profile.foreground,
                    fill=profile.accent,
                ),
                profile,
            )
        except ValueError:
            return 1
        return 0
    rows = [table.headers] + table.rows
    if not rows or not table.headers or box.w <= 32 * len(table.headers) or box.h <= 18 * len(rows):
        return sum(len(row) for row in rows) or 1
    font = role_font(profile, "table")[1]
    from .table_style import column_widths, row_heights

    widths = column_widths(rows, box.w, font, minimum_size)
    heights = row_heights(rows, widths, font, minimum_size, box.h)
    return sum(
        not table_cell_fits(value, font, minimum_size, widths[ci] - 16, heights[ri] - 12, ri == 0)
        for ri, row in enumerate(rows)
        for ci, value in enumerate(row)
    )


def table_region(table, pattern, profile, has_body, *, chart=False, body_texts=None):
    """Choose and validate the final reserved box, including any text split."""
    from .models import Box

    fields = {f["index"]: f for f in pattern.fields if f["role"] == "body"}
    regions = [
        (i, fields[i], b.model_copy()) for i, b in enumerate(pattern.body_zones) if i in fields
    ]
    # Only explicitly empty illustration fields, never arbitrary photo/artwork
    # zones. Preserve body indices so both engines keep prose in its own field.
    regions.extend(
        (len(pattern.body_zones) + f["index"], f, Box.model_validate(f["box"]))
        for f in pattern.fields
        if f["role"] == "image" and f.get("evidence_placeholder")
    )
    fitting = []
    for i, field, original in regions:
        box = original.model_copy()
        minimum = 0
        if chart:
            from .charts import chart_caption_layout

            text, height = chart_caption_layout([table.headers] + table.rows, box.w, profile)
            minimum = 160 + height + (12 if text else 0)
        if has_body and len(regions) == 1:
            # A fixed 60/40 split previously made every chart too short in
            # Workspace. Reserve the chart's real minimum before body fitting.
            if body_texts:
                from .field_style import styled_profile
                from .fonts import wrap_text

                text_profile, _ = styled_profile(profile, field.get("style", {}), "body")
                size = max(16, text_profile.body_size)
                text_height = sum(
                    len(wrap_text(text, text_profile.font_file, size, max(1, box.w - size * 1.4)))
                    * size
                    * 1.25
                    for text in body_texts
                ) + size * 0.5 * (len(body_texts) - 1)
                box.h = original.h - text_height - 12
                if box.h < minimum:
                    continue
            else:
                box.h = max(box.h * 0.6 - 12, minimum)
            if original.h - box.h - 12 < 15:
                continue
        if chart and (box.w < 260 or box.h < minimum):
            continue
        if not table_capacity(table, box, profile):
            fitting.append((i, field, box))
    if not fitting:
        raise ValueError(
            "Таблица не помещается ни в одно поле макета при минимальном читаемом размере"
        )
    return max(fitting, key=lambda item: item[2].w * item[2].h)


def object_contract(slide, package, pattern, images=()):
    """Server-owned assignments to exact source object IDs, with reserved media.

    New visual objects can occupy only an authored content field (or a picture
    placeholder). This contract never authorizes edits to background artwork.
    """
    from .models import Box

    fields = {f["role"] + ":" + str(f["index"]): f for f in pattern.fields}
    title = fields.get("title:0")
    if title is None:
        raise ValueError("В шаблоне не найден реальный объект заголовка")
    assignments = {
        str(f["shape_id"]): {
            "paragraphs": [],
            "box": f["box"],
            "role": f["role"],
            "font_size": f.get("font_size", 0),
        }
        for f in pattern.fields
        if f["role"] != "image"
    }
    assignments[str(title["shape_id"])]["paragraphs"] = [slide.title]
    visuals = []
    body, _ = body_and_title_sources(slide, package.content)
    tables = {t.id: t for t in package.content.tables}
    body = [f for f in body if f.source not in tables]
    regions = [
        (fields["body:" + str(i)], b.model_copy())
        for i, b in enumerate(pattern.body_zones)
        if "body:" + str(i) in fields
    ]
    if (body or slide.table_id or images) and not regions:
        raise ValueError("В шаблоне не найден реальный объект для содержания")
    # Reserve an actual body field for a native table/chart.
    if slide.table_id:
        table = tables[slide.table_id]
        selected, field, box = table_region(
            table,
            pattern,
            package.template,
            bool(body),
            chart=slide.layout == "chart",
            body_texts=[f.text for f in body],
        )
        regions = [(f, b) for f, b in regions if f["shape_id"] != field["shape_id"]]
        visuals.append(
            {
                "kind": "chart" if slide.layout == "chart" else "table",
                "table_id": slide.table_id,
                "shape_id": field["shape_id"],
                "box": box.model_dump(),
                "capacity_overflow": 0,
            }
        )
        if body and not regions:
            original = pattern.body_zones[selected]
            top = box.y + box.h + 12
            regions = [
                (field, Box(x=original.x, y=top, w=original.w, h=original.y + original.h - top))
            ]
    if images:
        from .native_template import intersects

        reserved = [Box.model_validate(v["box"]) for v in visuals]
        image_zones = [
            b
            for b in pattern.image_zones
            if b.w >= 40 and b.h >= 40 and not any(intersects(b, used) for used in reserved)
        ]
        if image_zones:
            visual = max(image_zones, key=lambda b: b.w * b.h).model_copy()
        elif len(regions) > 1:
            _, visual = regions.pop()
        elif regions:
            field, box = regions[0]
            gap = 12
            visual = Box(x=box.x + box.w * 0.55 + gap, y=box.y, w=box.w * 0.45 - gap, h=box.h)
            regions[0] = (field, Box(x=box.x, y=box.y, w=box.w * 0.55, h=box.h))
        else:
            raise ValueError("В выбранном макете нет независимого поля для таблицы и изображений")
        if visual.w < 40 or visual.h < 40:
            raise ValueError("Поле изображения слишком мало")
        gap = 10
        height = (visual.h - gap * (len(images) - 1)) / len(images)
        if height < 40:
            raise ValueError("Изображения не помещаются в поле макета")
        for i, asset in enumerate(images):
            visuals.append(
                {
                    "kind": "image",
                    "image_id": asset.id,
                    "box": Box(
                        x=visual.x, y=visual.y + i * (height + gap), w=visual.w, h=height
                    ).model_dump(),
                }
            )
    binding = bind_groups(slide, package, pattern)
    if not visuals and binding["status"] == "specialized":
        for i, group in enumerate(binding["groups"]):
            field = fields["body:" + str(i)]
            paragraphs = [f.text for f in group["facts"]]
            heading = fields.get("heading:" + str(i))
            if heading and heading["shape_id"] != field["shape_id"]:
                assignments[str(heading["shape_id"])]["paragraphs"] = [group["label"]]
            else:
                if inline_group_text(group["label"], paragraphs) != "\n".join(paragraphs):
                    paragraphs.insert(0, group["label"])
            assignments[str(field["shape_id"])]["paragraphs"] = paragraphs
    elif body:
        if not regions:
            raise ValueError("В макете нет поля для поясняющего текста")
        # No positional guess of comparison ownership in a generic contract.
        if slide.purpose in ("comparison", "process", "timeline"):
            regions = [max(regions, key=lambda pair: pair[1].w * pair[1].h)]
        count = min(len(regions), len(body))
        for i, (field, box) in enumerate(regions[:count]):
            group = body[i * len(body) // count : (i + 1) * len(body) // count]
            assignments[str(field["shape_id"])].update(
                paragraphs=[f.text for f in group], box=box.model_dump()
            )
    report = binding_report(slide, package, pattern, visuals)
    if visuals and slide.purpose in ("comparison", "process", "timeline") and not slide.table_id:
        report.update(
            status="general",
            reason="Media shares the authored content area; complete source text is kept together",
        )
    return {"fields": assignments, "visuals": visuals, "binding": report}
