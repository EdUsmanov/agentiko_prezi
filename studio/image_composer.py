"""Server-owned image placement. No image content or filenames drive model tools."""

import math
from .models import Box, Element, SlideScene


def contained(image, box):
    ratio = min(box.w / image.width, box.h / image.height)
    width, height = image.width * ratio, image.height * ratio
    return Box(x=box.x + (box.w - width) / 2, y=box.y + (box.h - height) / 2, w=width, h=height)


def compose_images(slide, package, index, variant, images):
    from .text_composer import text_element, fact_elements
    from .audit import audit_scenes
    from .contracts import candidates as semantic_candidates
    from .content_sources import package_sources

    p = package.template
    facts = {f.id: f for f in package.content.facts}
    tables = {t.id: t for t in package.content.tables}
    relevant, title_ids = package_sources(slide, package)
    from .semantic_bindings import labeled_facts

    body = labeled_facts([f for f in relevant if f.source not in tables], package)
    patterns = [
        pat
        for pat in semantic_candidates(package, slide, index, prefer_specialized=False)
        if pat.body_zones
    ]
    if slide.pattern_id == "token:auto":
        patterns = []
    elif slide.pattern_id is not None:
        patterns = [pat for pat in patterns if pat.id == slide.pattern_id]
    candidates = []
    for pattern in patterns or [None]:
        color = pattern.foreground or p.foreground if pattern else p.foreground
        background = pattern.background or p.background if pattern else p.background
        title_zone = (
            pattern.title_zone
            if pattern
            else Box(x=p.margin, y=p.margin, w=p.width - 2 * p.margin, h=p.height * 0.19)
        )
        title = text_element(
            slide.title,
            title_zone,
            p,
            "title",
            pattern.title_size or p.title_size if pattern else p.title_size,
            color=pattern.title_foreground or color if pattern else color,
        )
        title.background_hint = pattern.title_background if pattern else ""
        title.source_ids = title_ids
        # Restrict both panels to ONE actual source container, never across artwork gaps.
        zone = (
            max(pattern.body_zones, key=lambda b: b.w * b.h)
            if pattern
            else Box(
                x=p.margin,
                y=title_zone.y + title_zone.h + 16,
                w=p.width - 2 * p.margin,
                h=p.height - p.margin - title_zone.y - title_zone.h - 16,
            )
        )
        gap = min(20, zone.w * 0.035)
        image_fraction = {"executive": 0.38, "analytical": 0.44, "story": 0.48}[variant]
        image_w = (zone.w - gap) * image_fraction
        text_w = zone.w - gap - image_w
        image_left = variant == "story"
        visual = Box(
            x=zone.x if image_left else zone.x + text_w + gap, y=zone.y, w=image_w, h=zone.h
        )
        columns = 2 if len(images) > 2 else 1
        rows = math.ceil(len(images) / columns)
        if min(text_w, image_w - gap * (columns - 1), zone.h - gap * (rows - 1)) <= 1:
            continue
        text_zone = Box(
            x=zone.x + image_w + gap if image_left else zone.x, y=zone.y, w=text_w, h=zone.h
        )
        if pattern and len(pattern.body_zones) >= 2:
            # Use two authored containers instead of cramming everything into one card.
            zones = sorted(pattern.body_zones, key=lambda b: (b.y, b.x))
            text_zone, visual = zones[0], zones[1]
            if image_left:
                visual, text_zone = zones[0], zones[1]
        image_zones = [b for b in pattern.image_zones if b.w >= 40 and b.h >= 40] if pattern else []
        if image_zones:
            text_zone, visual = zone, max(image_zones, key=lambda b: b.w * b.h)
        elements = [title]
        if pattern and pattern.background_image:
            elements.insert(
                0,
                Element(
                    kind="image",
                    box=Box(x=0, y=0, w=p.width, h=p.height),
                    image_path=pattern.background_image,
                    role="template_background",
                ),
            )
        if slide.table_id:
            table = tables[slide.table_id]
            table_height = text_zone.h * (0.6 if body else 1)
            elements.append(
                Element(
                    kind="table",
                    box=Box(x=text_zone.x, y=text_zone.y, w=text_zone.w, h=table_height),
                    rows=[table.headers] + table.rows,
                    font=p.font,
                    size=p.body_size,
                    color=color,
                    fill=p.accent,
                    source_ids=[f for f in slide.fact_ids if facts[f].source == table.id],
                )
            )
            if body:
                body_zone = Box(
                    x=text_zone.x,
                    y=text_zone.y + table_height + gap,
                    w=text_zone.w,
                    h=max(1, text_zone.h - table_height - gap),
                )
                elements.extend(fact_elements(body, body_zone, p, color))
        else:
            elements.extend(fact_elements(body, text_zone, p, color))
        cell_w = (visual.w - gap * (columns - 1)) / columns
        cell_h = (visual.h - gap * (rows - 1)) / rows
        for i, image in enumerate(images):
            box = Box(
                x=visual.x + (i % columns) * (cell_w + gap),
                y=visual.y + (i // columns) * (cell_h + gap),
                w=cell_w,
                h=cell_h,
            )
            elements.append(
                Element(
                    kind="image",
                    box=contained(image, box),
                    image_path=image.path,
                    image_id=image.id,
                    role="user_image",
                    text=image.caption,
                )
            )
        if pattern:
            z = pattern.body_zones.index(zone)
            for e in elements:
                if (
                    e.kind in ("text", "table")
                    and e.role != "title"
                    and z < len(pattern.zone_backgrounds)
                ):
                    e.background_hint = pattern.zone_backgrounds[z]
                    e.color = pattern.zone_foregrounds[z]
        else:
            elements.extend(
                Element(kind="image", box=a.box, image_path=a.path, role="brand") for a in p.assets
            )
        scene = SlideScene(
            title=slide.title,
            background=background,
            elements=elements,
            source_ids=slide.fact_ids,
            layout=slide.layout,
            purpose=slide.purpose,
            pattern_id=pattern.id if pattern else None,
            strategy="native_template" if pattern else "token_composition",
            notes="\n".join(
                f"[{fid}] {facts[fid].source}: {facts[fid].text}" for fid in slide.fact_ids
            ),
        )
        geometry = {
            "text_overflow",
            "table_overflow",
            "out_of_bounds",
            "overlap",
            "container_overflow",
        }
        score = 1000 * sum(
            f.code in geometry and f.severity == "error" for f in audit_scenes([scene], package)
        )
        score += sum(
            max(0, min(p.body_size, 16) - e.size) * 10 for e in elements if e.kind == "text"
        )
        if pattern:
            score += max(0, len(pattern.body_zones) - 2) * 40
        candidates.append((score, scene))
    if not candidates:
        raise ValueError("В выбранном макете нет безопасной области для картинок и текста")
    return min(candidates, key=lambda pair: pair[0])[1]
