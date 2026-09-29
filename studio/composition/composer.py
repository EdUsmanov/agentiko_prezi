from studio.composition.text_composer import (
    text_element as text_element,
    fact_elements as fact_elements,
)
from studio.models import Box, Element, SlideScene
from studio.templates.fonts import wrap_text, role_font, element_font, table_cell_fits
from studio.contents.parsing import numeric_column
from studio.templates.template_geometry import contrast, minimum_text_contrast
from studio.composition.contracts import body_and_title_sources, candidates as semantic_candidates
from studio.templates.field_style import field_style, styled_profile
from studio.composition.table_style import column_widths, row_heights


def compose(slide, package, index, variant):
    if slide.layout == "divider":
        p = package.template
        candidates = semantic_candidates(package, slide, index)
        if slide.pattern_id:
            candidates = [x for x in candidates if x.id == slide.pattern_id]
        if not candidates:
            raise ValueError("Макет разделителя отсутствует в шаблоне")
        options = [
            (
                pattern,
                text_element(
                    slide.title,
                    pattern.title_zone,
                    p,
                    "title",
                    pattern.title_size or p.title_size,
                    color=pattern.title_foreground or p.foreground,
                    field_style=field_style(pattern, "title"),
                ),
            )
            for pattern in candidates
        ]
        pattern, title = max(options, key=lambda pair: pair[1].size)
        title.background_hint = pattern.title_background
        elements = [title]
        if pattern.background_image:
            elements.insert(
                0,
                Element(
                    kind="image",
                    box=Box(x=0, y=0, w=p.width, h=p.height),
                    image_path=pattern.background_image,
                    role="template_background",
                ),
            )
        return SlideScene(
            title=slide.title,
            background=pattern.background or p.background,
            elements=elements,
            source_ids=[],
            layout="divider",
            purpose="divider",
            pattern_id=pattern.id,
            strategy="native_template",
        )
    table_ids = {t.id for t in package.content.tables}
    sources = {
        f.source for f in package.content.facts if f.id in slide.fact_ids and f.source in table_ids
    }
    if sources and sources != {slide.table_id}:
        raise ValueError(
            "Факты таблицы должны быть представлены соответствующей таблицей или графиком"
        )
    native = (
        None if slide.pattern_id == "token:auto" else compose_native(slide, package, index, variant)
    )
    if native is not None:
        return native
    p = package.template
    w, h, m = p.width, p.height, p.margin
    _facts = {f.id: f for f in package.content.facts}
    relevant, title_ids = body_and_title_sources(slide, package.content)
    tables = {t.id: t for t in package.content.tables}
    elements = []
    top_assets = [a for a in p.assets if a.box.y < h * 0.15]
    title_y = max([m] + [a.box.y + a.box.h + 12 for a in top_assets])
    title_h = min(
        h * 0.23,
        max(
            p.title_size * 1.3,
            len(wrap_text(slide.title, role_font(p, "title")[1], p.title_size, w - 2 * m))
            * p.title_size
            * 1.25,
        ),
    )
    elements.append(
        text_element(
            slide.title,
            Box(x=m, y=title_y, w=w - 2 * m, h=title_h),
            p,
            "title",
            p.title_size,
            source_ids=title_ids,
        )
    )
    y = title_y + title_h + h * 0.055
    bottom = min([h - m] + [a.box.y - 12 for a in p.assets if a.box.y > h * 0.8])
    content_h = bottom - y
    if content_h < h * 0.3:
        raise ValueError("Шаблон не оставляет достаточно места для заголовка и содержания")
    gap = w * 0.025
    # Every fact is rendered, or represented by its full source table.
    body = [f for f in relevant if f.source not in tables]
    if slide.table_id:
        table = tables[slide.table_id]
        numeric = numeric_column(table)
        other_tables = [f for f in relevant if f.source in tables and f.source != slide.table_id]
        if other_tables:
            raise ValueError("Несколько таблиц на одном слайде: увеличьте число слайдов")
        visual_w = w - 2 * m if not body else (w - 2 * m) * 0.62
        table_x = m
        if variant == "story" and slide.layout != "chart":
            visual_w *= 0.86
            if not body:
                table_x = m + (w - 2 * m - visual_w) / 2
        elif variant == "executive" and not body and slide.layout != "chart":
            visual_w *= 0.94
        box = Box(x=table_x, y=y, w=visual_w, h=content_h)
        if slide.layout == "chart" and numeric:
            column, values, unit = numeric
            elements.append(
                Element(
                    kind="chart",
                    box=box,
                    labels=[r[0] for r in table.rows],
                    values=values,
                    value_labels=[r[column] for r in table.rows],
                    unit=table.headers[column] + (f" ({unit})" if unit else ""),
                    font=p.font,
                    size=p.body_size,
                    color=p.foreground,
                    fill=p.accent,
                    source_ids=[f.id for f in relevant if f.source == table.id],
                )
            )
            # Charts must not hide other columns from the source table.
            if len(table.headers) > 2:
                elements[-1] = Element(
                    kind="table",
                    box=box,
                    rows=[table.headers] + table.rows,
                    font=p.font,
                    size=p.body_size,
                    color=p.foreground,
                    fill=p.accent,
                    source_ids=[f.id for f in relevant if f.source == table.id],
                )
        else:
            elements.append(
                Element(
                    kind="table",
                    box=box,
                    rows=[table.headers] + table.rows,
                    font=p.font,
                    size=p.body_size,
                    color=p.foreground,
                    fill=p.accent,
                    source_ids=[f.id for f in relevant if f.source == table.id],
                )
            )
        if body:
            x = m + visual_w + gap
            elements.extend(
                fact_elements(body, Box(x=x, y=y, w=w - m - x, h=content_h), p, p.foreground)
            )
    elif slide.layout == "columns" and len(body) > 1:
        columns = min(len(body), 3)
        col_w = (w - 2 * m - gap * (columns - 1)) / columns
        for c in range(columns):
            group = body[c * len(body) // columns : (c + 1) * len(body) // columns]
            elements.append(
                Element(
                    kind="line", box=Box(x=m + c * (col_w + gap), y=y, w=col_w, h=1), color=p.accent
                )
            )
            elements.extend(
                fact_elements(
                    group,
                    Box(x=m + c * (col_w + gap), y=y + 14, w=col_w, h=content_h - 14),
                    p,
                    p.foreground,
                )
            )
    elif slide.layout == "split" and len(body) > 1:
        left_w = (w - 2 * m - gap) * 0.48
        elements.append(
            text_element(
                body[0].text,
                Box(x=m, y=y, w=left_w, h=content_h),
                p,
                size=min(p.font_sizes, key=lambda s: abs(s - p.body_size * 1.3)),
                source_ids=[body[0].id],
            )
        )
        elements.extend(
            fact_elements(
                body[1:],
                Box(x=m + left_w + gap, y=y, w=w - 2 * m - left_w - gap, h=content_h),
                p,
                p.foreground,
            )
        )
    elif slide.layout == "evidence":
        # Flat rows: evidence references on the left, complete source text on the right.
        row_h = content_h / max(len(body), 1)
        for i, f in enumerate(body):
            _label_color = p.accent if contrast(p.accent, p.background) >= 4.5 else p.foreground
            elements.append(
                text_element(
                    f.text,
                    Box(x=m + w * 0.1, y=y + i * row_h, w=w - 2 * m - w * 0.1, h=row_h - 12),
                    p,
                    source_ids=[f.id],
                )
            )
    elif slide.layout == "process" and len(body) >= 2:
        row_h = content_h / len(body)
        for i, f in enumerate(body):
            elements.append(
                text_element(
                    str(i + 1), Box(x=m, y=y + i * row_h, w=w * 0.07, h=row_h - 8), p, "label"
                )
            )
            elements.append(
                text_element(
                    f.text,
                    Box(x=m + w * 0.1, y=y + i * row_h, w=w - 2 * m - w * 0.1, h=row_h - 8),
                    p,
                    source_ids=[f.id],
                )
            )
    else:
        text = "\n\n".join(f.text for f in body)
        size = p.body_size
        text_w = w - 2 * m
        if slide.layout == "statement":
            size = min(p.font_sizes, key=lambda s: abs(s - p.body_size * 1.6))
            text_w *= 0.92
        elif slide.layout == "split":
            # Single-fact variant: different composition without duplicated evidence.
            text_w *= 0.72
        if len(body) > 1:
            elements.extend(
                fact_elements(body, Box(x=m, y=y, w=text_w, h=content_h), p, p.foreground)
            )
        else:
            elements.append(
                text_element(
                    text,
                    Box(x=m, y=y, w=text_w, h=content_h),
                    p,
                    size=size,
                    source_ids=[f.id for f in body],
                )
            )
    footer_size = min(p.font_sizes, key=lambda s: abs(s - 10))
    footer = text_element(
        f"{index + 1:02d}",
        Box(x=w - m - 35, y=h - max(m * 0.6, footer_size * 1.5 + 4), w=35, h=footer_size * 1.5),
        p,
        "footer",
        footer_size,
    )
    if contrast(footer.color, p.background) < minimum_text_contrast(footer.size, footer.bold):
        readable = [
            color
            for color in p.colors
            if contrast(color, p.background) >= minimum_text_contrast(footer.size, footer.bold)
        ]
        if readable:
            original = tuple(int(footer.color[i : i + 2], 16) for i in (1, 3, 5))
            footer.color = min(
                readable,
                key=lambda color: sum(
                    (int(color[i : i + 2], 16) - original[channel]) ** 2
                    for channel, i in enumerate((1, 3, 5))
                ),
            )
    elements.append(footer)
    for asset in p.assets:
        elements.append(Element(kind="image", box=asset.box, image_path=asset.path, role="brand"))
    # Adapt safe text zones from an observed ordinary slide when they fit.
    # Otherwise use the token-derived composition above. No source wording is copied.
    body_elements = [e for e in elements if e.kind == "text" and e.role == "body"]
    pattern_id = None
    if slide.layout in ("split", "columns") and len(body_elements) in (2, 3):
        for pattern in semantic_candidates(package, slide, index):
            zones = [
                b
                for b in pattern.text_zones
                if b.y >= y
                and b.y + b.h <= bottom + 1
                and b.x >= m * 0.8
                and b.x + b.w <= w - m * 0.8
                and b.w >= w * 0.2
                and b.h >= h * 0.2
            ]
            if len(zones) != len(body_elements):
                continue
            from studio.checks.audit import overlaps

            if any(overlaps(a, b) for i, a in enumerate(zones) for b in zones[i + 1 :]):
                continue
            zones.sort(key=lambda b: (b.x, b.y))
            if all(
                len(wrap_text(e.text, p.font_file, e.size, b.w)) * e.size * 1.25 <= b.h
                for e, b in zip(body_elements, zones)
            ):
                for e, b in zip(body_elements, zones):
                    e.box = b
                pattern_id = pattern.id
                break
    return SlideScene(
        title=slide.title,
        background=p.background,
        elements=elements,
        source_ids=slide.fact_ids,
        layout=slide.layout,
        purpose=slide.purpose,
        pattern_id=pattern_id,
        strategy="exemplar_zones" if pattern_id else "token_composition",
        notes="\n".join(f"[{f.id}] {f.source}, строка {f.line}: {f.text}" for f in relevant),
    )


def _compose_slide(variant, package, index, image_groups=None):
    """Same complete postprocessing as a deck, for one slide at its original index."""
    from studio.contents.uploads import assign_images
    from studio.composition.image_composer import compose_images

    image_groups = assign_images(package, variant) if image_groups is None else image_groups
    slide = variant.slides[index]
    scene = (
        compose_images(slide, package, index, variant.key, image_groups[index])
        if image_groups[index]
        else compose(slide, package, index, variant.key)
    )
    if slide.table_id:
        from studio.composition.contracts import normalized

        table = next(t for t in package.content.tables if t.id == slide.table_id)
        cells = {normalized(c) for row in [table.headers] + table.rows for c in row}
        aliases = [
            f.id
            for f in package.content.facts
            if f.id in slide.fact_ids and normalized(f.text) in cells
        ]
        for element in scene.elements:
            if element.kind in ("table", "chart") or element.role.startswith("metric_"):
                element.source_ids = list(dict.fromkeys(element.source_ids + aliases))
    if slide.layout == "chart" and slide.table_id:
        from studio.composition.charts import make_chart

        table = next(t for t in package.content.tables if t.id == slide.table_id)
        for i, element in enumerate(scene.elements):
            if element.kind in ("table", "chart") and element.source_ids:
                scene.elements[i] = make_chart(
                    table, slide, element.box, package.template, element.color, element.source_ids
                )
                scene.elements[i].background_hint = element.background_hint
                scene.elements[i].field_style = element.field_style
    _, title_ids = body_and_title_sources(slide, package.content)
    for element in scene.elements:
        if element.role == "title":
            element.source_ids = title_ids
    for element in scene.elements:
        if element.kind == "chart":
            table = next(t for t in package.content.tables if t.id == slide.table_id)
            element.chart_type = slide.chart_type if slide.chart_type != "auto" else "bar"
            element.category_title = table.headers[0]
    for element in scene.elements:
        if element.kind in ("text", "table", "chart"):
            element.font = element_font(package.template, element)[0]
        if element.kind in ("table", "chart"):
            from studio.composition.table_style import apply_table_style

            pattern = next((p for p in package.template.patterns if p.id == scene.pattern_id), None)
            # Keep chart bar colors; style any later chart-to-table fallback in repair_scenes.
            if element.kind == "table":
                apply_table_style(element, pattern)
    import re

    # An approved brief is the exact content boundary; removed source URLs
    # must not reappear in notes or the exported PPTX.
    original = (
        package.content
        if getattr(package, "input_mode", "content") == "brief"
        else package.original_content or package.content
    )
    urls = list(
        dict.fromkeys(
            url.rstrip(".,;]")
            for fact in original.facts
            for url in re.findall(r"https?://[^\s)<>]+", fact.text)
        )
    )
    if urls:
        scene.notes += "\nИсточники:\n" + "\n".join(urls)
    from studio.composition.image_composer import attach_template_resources

    attach_template_resources(scene, package, slide)
    return scene


def compose_slide(variant, package, index, image_groups=None):
    """Try deterministic geometry before requesting any editorial rewrite."""
    scene = _compose_slide(variant, package, index, image_groups)
    slide = variant.slides[index]
    if slide.pattern_id is None and slide.layout == "chart":
        from studio.composition.render import chart_fits

        charts = [e for e in scene.elements if e.kind == "chart"]
        if any(not chart_fits(e, package.template) for e in charts):
            alternate = variant.model_copy(deep=True)
            alternate.slides[index].pattern_id = "token:auto"
            try:
                candidate = _compose_slide(alternate, package, index, image_groups)
            except ValueError:
                # An unavailable token layout must not discard the original scene.
                return scene
            candidate_charts = [e for e in candidate.elements if e.kind == "chart"]
            from studio.checks.quality import candidate_regressions

            if (
                len(candidate_charts) == len(charts)
                and all(chart_fits(e, package.template) for e in candidate_charts)
                and not candidate_regressions([scene], [candidate], package)
            ):
                candidate.notes += "\nChart layout adapted using template fonts and palette: source fields cannot fit its labels."
                return candidate
    if (
        slide.pattern_id is not None
        or not scene.pattern_id
        or slide.purpose in ("cover", "divider")
    ):
        return scene
    from studio.checks.audit import audit_scenes, repair_scenes

    from studio.checks.repair_policy import FIT_CODES

    def defects(candidate):
        repair_scenes([candidate], package)
        return [f for f in audit_scenes([candidate], package) if f.code in FIT_CODES]

    if not defects(scene.model_copy(deep=True)):
        return scene
    alternate = variant.model_copy(deep=True)
    alternate.slides[index].pattern_id = "token:auto"
    try:
        candidate = _compose_slide(alternate, package, index, image_groups)
        if not defects(candidate):
            candidate.notes += "\nLayout adapted from template fonts and palette: authored fields could not fit the content."
            return candidate
    except ValueError:
        pass
    return scene


def compose_variant(variant, package):
    from studio.contents.uploads import assign_images

    images = assign_images(package, variant)
    return [compose_slide(variant, package, i, images) for i in range(len(variant.slides))]


class CompositionSession:
    """Job-scoped candidates for a frozen package; returned scenes are independent copies.

    Create a new session when content/template changes. Layout edits only change
    plans, which are part of each key. Nothing is cached across jobs or persisted.
    """

    def __init__(self, package):
        self.package = package
        self.scenes = {}
        self.image_assignments = {}
        self.hits = 0
        self.misses = 0

    def slide(self, variant, index):
        from studio.contents.uploads import assign_images

        variant_key = variant.model_dump_json()
        if variant_key not in self.image_assignments:
            self.image_assignments[variant_key] = assign_images(self.package, variant)
        groups = self.image_assignments[variant_key]
        key = (
            variant.key,
            index,
            variant.slides[index].model_dump_json(),
            tuple(a.model_dump_json() for a in groups[index]),
        )
        if key not in self.scenes:
            self.scenes[key] = compose_slide(variant, self.package, index, groups)
            self.misses += 1
        else:
            self.hits += 1
        return self.scenes[key].model_copy(deep=True)

    def variant(self, variant):
        return [self.slide(variant, i) for i in range(len(variant.slides))]


def compose_native(slide, package, index, variant):
    """Fit editable content into source zones; never substitute a generic full-slide design."""
    p = package.template
    patterns = semantic_candidates(package, slide, index)
    if slide.pattern_id is not None:
        patterns = [pattern for pattern in patterns if pattern.id == slide.pattern_id]
        if not patterns:
            raise ValueError("Выбранная композиция отсутствует в шаблоне")
    if not patterns and slide.purpose == "cover":
        raise ValueError("В шаблоне нет допустимого титульного макета")
    if not patterns:
        return None
    _facts = {f.id: f for f in package.content.facts}
    tables = {t.id: t for t in package.content.tables}
    relevant, title_ids = body_and_title_sources(slide, package.content)
    body = [f for f in relevant if f.source not in tables]
    if len({f.source for f in relevant if f.source in tables}) > 1:
        raise ValueError("Несколько таблиц на одном слайде: увеличьте число слайдов")
    desired = (
        min(len(body), 3)
        if len(body) > 1 and slide.layout in ("columns", "process")
        else 2
        if len(body) > 1 and slide.layout == "split"
        else 1
    )
    if slide.table_id:
        desired = 2 if body else 1

    def zones_for(pattern):
        return pattern.body_zones

    def elements_for(pattern):
        foreground = pattern.foreground or p.foreground
        title = text_element(
            slide.title,
            pattern.title_zone,
            p,
            "title",
            pattern.title_size or p.title_size,
            color=pattern.title_foreground or foreground,
            source_ids=title_ids,
            field_style=field_style(pattern, "title"),
        )
        title.background_hint = pattern.title_background
        elements = [title]
        zones = zones_for(pattern)
        from studio.contents.semantic_bindings import bind_groups

        binding = bind_groups(slide, package, pattern)
        if len(zones) == len(pattern.body_zones) and binding["status"] != "specialized":
            for i, zone in enumerate(pattern.number_zones[: min(len(zones), len(body))]):
                if zone:
                    label = text_element(
                        f"{i + 1:02d}",
                        zone,
                        p,
                        "label",
                        min(p.title_size, zone.h / 1.25),
                        color=foreground,
                    )
                    label.bold = True
                    elements.append(label)
        if slide.table_id:
            from studio.contents.semantic_bindings import table_region

            table = tables[slide.table_id]
            try:
                selected, selected_field, b = table_region(
                    table,
                    pattern,
                    p,
                    bool(body),
                    chart=slide.layout == "chart",
                    body_texts=[f.text for f in body],
                )
            except ValueError:
                return None
            data_profile, data_style = styled_profile(p, selected_field.get("style", {}), "table")
            _numeric = numeric_column(table)
            if slide.layout != "chart":
                rows = [table.headers] + table.rows
                widths = column_widths(rows, b.w, data_profile.font_file, data_profile.body_size)
                natural = sum(
                    row_heights(rows, widths, data_profile.font_file, data_profile.body_size)
                )
                b = b.model_copy(update={"h": min(b.h, natural)})
            if slide.layout == "chart":
                from studio.composition.charts import make_chart

                elements.append(
                    make_chart(
                        table,
                        slide,
                        b,
                        data_profile,
                        foreground,
                        [f.id for f in relevant if f.source == table.id],
                    )
                )
                elements[-1].field_style = data_style
            else:
                element = Element(
                    kind="table",
                    box=b,
                    rows=[table.headers] + table.rows,
                    font=p.font,
                    size=data_profile.body_size,
                    color=foreground,
                    fill=p.accent,
                    field_style=data_style,
                    source_ids=[f.id for f in relevant if f.source == table.id],
                )
                if table.visualization == "metrics":
                    from studio.composition.metrics import metric_elements

                    try:
                        elements.extend(metric_elements(element, p))
                    except ValueError:
                        return None
                else:
                    elements.append(element)
            if body:
                remaining = [zone for i, zone in enumerate(pattern.body_zones) if i != selected]
                if remaining:
                    text_zone = max(remaining, key=lambda zone: zone.w * zone.h)
                else:
                    original = pattern.body_zones[selected]
                    top = b.y + b.h + 12
                    text_zone = Box(
                        x=original.x, y=top, w=original.w, h=original.y + original.h - top
                    )
                elements.extend(
                    fact_elements(
                        body,
                        text_zone,
                        p,
                        foreground,
                        field_style=field_style(
                            pattern,
                            "body",
                            next(
                                (i for i, z in enumerate(pattern.body_zones) if z == text_zone), 0
                            ),
                        ),
                    )
                )
        else:
            if binding["status"] == "specialized":
                for group_index, group in enumerate(binding["groups"]):
                    bounds = group["box"].model_copy()
                    heading = group["heading"]
                    if heading is None:
                        # Small native cards cannot afford a separate, tiny caption.
                        # Keep the step/date label and evidence in one readable field.
                        from studio.contents.semantic_bindings import inline_group_text

                        text = inline_group_text(group["label"], [f.text for f in group["facts"]])
                        elements.append(
                            text_element(
                                text,
                                bounds,
                                p,
                                "body",
                                size=p.body_size,
                                color=foreground,
                                source_ids=[f.id for f in group["facts"]],
                                field_style=field_style(pattern, "body", group_index),
                            )
                        )
                        continue
                    label = text_element(
                        group["label"],
                        heading,
                        p,
                        "subheading",
                        size=p.body_size,
                        color=foreground,
                        field_style=field_style(pattern, "heading", group_index),
                    )
                    label.bold = True
                    elements.append(label)
                    elements.extend(
                        fact_elements(
                            group["facts"],
                            bounds,
                            p,
                            foreground,
                            field_style=field_style(pattern, "body", group_index),
                        )
                    )
                count = 0
            else:
                count = (
                    1
                    if slide.purpose in ("comparison", "process", "timeline") and body
                    else min(len(zones), len(body))
                )
            for i, b in enumerate(zones[:count]):
                if slide.layout == "split" and count == 2:
                    group = body[:1] if i == 0 else body[1:]
                else:
                    group = body[i * len(body) // count : (i + 1) * len(body) // count]
                heading = (
                    pattern.heading_zones[i]
                    if len(zones) == len(pattern.body_zones) and i < len(pattern.heading_zones)
                    else None
                )
                elements.extend(
                    fact_elements(group, b, p, foreground, heading, field_style(pattern, "body", i))
                )
        for e in elements[1:]:
            zone_index = next(
                (
                    i
                    for i, b in enumerate(pattern.body_zones)
                    if b.x <= e.box.x + 1
                    and b.y <= e.box.y + 1
                    and b.x + b.w >= e.box.x + e.box.w - 1
                ),
                0,
            )
            if zone_index < len(pattern.zone_backgrounds):
                e.background_hint = pattern.zone_backgrounds[zone_index]
            if zone_index < len(pattern.zone_foregrounds) and pattern.zone_foregrounds[zone_index]:
                e.color = pattern.zone_foregrounds[zone_index]
        return elements

    options = []
    meanings = {
        item["pattern_id"]: item
        for item in package.analysis.get("template_semantics", {}).get("patterns", [])
    }
    for pattern in patterns:
        elements = elements_for(pattern)
        if elements is None:
            continue
        scoring_elements = []
        chart_penalty = 0
        for e in elements:
            if e.kind == "chart":
                from studio.composition.render import chart_fits

                if not chart_fits(e, p):
                    chart_penalty += 10000
                    table = tables[slide.table_id]
                    e = e.model_copy(update={"kind": "table", "rows": [table.headers] + table.rows})
            scoring_elements.append(e)
        overflow = sum(
            max(
                0,
                len(
                    wrap_text(
                        e.text,
                        element_font(p, e)[1],
                        e.size,
                        (e.box.w - (e.size * 1.4 if e.bullet else 0))
                        * (0.94 if e.bold or e.bold_prefix else 1),
                    )
                )
                * e.size
                * 1.25
                - e.box.h,
            )
            for e in scoring_elements
            if e.kind == "text"
        )
        # A correctly sized text box can still have been positioned outside its
        # authored container. Include that defect in layout selection too.
        for e in elements:
            if e.kind == "text" and e.role == "body":
                if not any(
                    e.box.x >= z.x - 0.5
                    and e.box.y >= z.y - 0.5
                    and e.box.x + e.box.w <= z.x + z.w + 0.5
                    and e.box.y + e.box.h <= z.y + z.h + 0.5
                    for z in pattern.body_zones
                ):
                    overflow += e.box.h + 100
        for e in scoring_elements:
            if e.kind == "table":
                # A small text zone is not automatically a safe table zone.
                # Compare overflow after the same bounded font repair used later.
                sizes = sorted(
                    {e.size, *[s for s in [16, *p.font_sizes] if 10 <= s <= e.size]}, reverse=True
                )
                for size in sizes:
                    widths = column_widths(e.rows, e.box.w, element_font(p, e)[1], size)
                    heights = row_heights(e.rows, widths, element_font(p, e)[1], size, e.box.h)
                    table_overflow = max(
                        0, sum(row_heights(e.rows, widths, element_font(p, e)[1], size)) - e.box.h
                    )
                    table_overflow += sum(
                        20
                        for ri, row in enumerate(e.rows)
                        for ci, cell in enumerate(row)
                        if not table_cell_fits(
                            cell,
                            element_font(p, e)[1],
                            size,
                            widths[ci] - 16,
                            heights[ri] - 12,
                            ri == 0,
                        )
                    )
                    if table_overflow == 0:
                        break
                overflow += table_overflow
        # Native template scales below the global body median are not defects.
        # Penalize genuinely small type, not readable 16–20pt layouts with artwork.
        small = sum(
            max(0, min(p.body_size, 16) - e.size)
            for e in elements
            if e.role == "body" and e.kind == "text"
        )
        count_penalty = abs(min(len(zones_for(pattern)), len(body) or 1) - desired) * 2
        occupied = (1 + bool(body)) if slide.table_id else len(body)
        empty_regions = max(0, len(zones_for(pattern)) - occupied)
        # Prioritize readability, then matching template examples, then requested composition.
        meaning = meanings.get(pattern.id)
        role_penalty = 0 if not meaning or slide.role in meaning["roles"] else 4
        title_penalty = sum(
            max(0, min(p.title_size, 16) - e.size) * 5 for e in elements if e.role == "title"
        )
        cover_penalty = (
            (-20 if index == 0 and len(body) <= 2 else 500) if pattern.role == "cover" else 0
        )
        observed_masters = {p.master_index for p in patterns if p.source_slide}
        unobserved_master = (
            40 if observed_masters and pattern.master_index not in observed_masters else 0
        )
        from studio.templates.template_geometry import minimum_text_contrast

        contrast_penalty = (
            sum(
                contrast(e.color, e.background_hint or pattern.background or p.background)
                < minimum_text_contrast(e.size, e.bold)
                for e in elements
                if e.kind == "text"
            )
            * 500
        )
        from studio.checks.quality import scene_quality_findings

        probe = SlideScene(
            title=slide.title,
            background=pattern.background or p.background,
            elements=elements,
            source_ids=slide.fact_ids,
            layout=slide.layout,
            purpose=slide.purpose,
            pattern_id=pattern.id,
        )
        # Table fitting can lower the font after composition. Score the same
        # repaired geometry that the preparation/generation audits will see,
        # otherwise a cramped 16pt table wins and later becomes unreadable.
        from studio.checks.audit import repair_scenes

        repaired_probe = probe.model_copy(deep=True)
        repair_scenes([repaired_probe], package)
        policy_findings = scene_quality_findings([repaired_probe], package)
        policy_penalty = sum(
            {
                "readability": 120,
                "unused_template_regions": 100,
                "unsafe_text_zone": 40,
                "local_font_unresolved": 40,
            }.get(f.code, 0)
            for f in policy_findings
        )
        score = (
            policy_penalty
            + contrast_penalty
            + overflow * 100
            + chart_penalty
            + empty_regions * 40
            + small * 2
            + count_penalty
            + (0 if pattern.source_slide else 5)
            + unobserved_master
            + role_penalty
            + title_penalty
            + cover_penalty
        )
        # Explicit unknown safety is weaker than checked fields, not proof of safety.
        score += (
            sum(
                row.get("status") == "unknown"
                for row in pattern.safe_text_zone.get("field_checks", [])
            )
            * 40
        )
        score += sum(bool(e.field_style.get("unresolved_font")) for e in elements) * 40
        score -= min(pattern.graphic_count, 6) * 0.5
        # A semantically exact layout wins comparable geometry, but never a
        # readability regression: overflow still dominates this bounded penalty.
        if slide.purpose not in ("auto", "content") and pattern.purpose != slide.purpose:
            score += 60
        options.append((score, pattern, elements))
    if not options:
        if slide.pattern_id is not None:
            raise ValueError("Выбранная композиция несовместима с содержимым")
        return None
    options.sort(key=lambda item: item[0])
    best = options[0][0]
    equivalent = [item for item in options if item[0] <= best + 1]
    offset = {"executive": 0, "analytical": 1, "story": 2}[variant]
    _, pattern, elements = equivalent[(index + offset) % len(equivalent)]
    if pattern.background_image:
        elements.insert(
            0,
            Element(
                kind="image",
                box=Box(x=0, y=0, w=p.width, h=p.height),
                image_path=pattern.background_image,
                role="template_background",
            ),
        )
    return SlideScene(
        title=slide.title,
        background=pattern.background or p.background,
        elements=elements,
        source_ids=slide.fact_ids,
        layout=slide.layout,
        purpose=slide.purpose,
        pattern_id=pattern.id,
        strategy="native_template",
        notes="\n".join(f"[{f.id}] {f.source}, строка {f.line}: {f.text}" for f in relevant),
    )
