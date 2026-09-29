"""Server-owned image placement. No image content or filenames drive model tools."""

import math
import re
from pathlib import Path
from studio.models import Box, Element, SlideScene


def contained(image, box):
    ratio = min(box.w / image.width, box.h / image.height)
    width, height = image.width * ratio, image.height * ratio
    return Box(x=box.x + (box.w - width) / 2, y=box.y + (box.h - height) / 2, w=width, h=height)


def fit_box(source, target):
    ratio = min(target.w / source.w, target.h / source.h)
    width, height = source.w * ratio, source.h * ratio
    return Box(
        x=target.x + (target.w - width) / 2, y=target.y + (target.h - height) / 2, w=width, h=height
    )


def _resource_preview_matches_background(path, background):
    from PIL import Image

    try:
        with Image.open(path) as image:
            pixel = image.convert("RGBA").getpixel((0, 0))
    except (OSError, ValueError):
        return False
    if pixel[3] < 32:
        return True
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", background):
        return False
    expected = tuple(int(background[i : i + 2], 16) for i in (1, 3, 5))
    return max(abs(pixel[i] - expected[i]) for i in range(3)) <= 8


def attach_template_resources(scene, package, slide):
    """Use only semantically matched catalog assets with a measured free slot."""
    profile = package.template
    if scene.purpose in ("cover", "divider"):
        return scene
    by_image = {image.id: image for image in package.images}
    if not profile.resources:
        if any(
            by_image.get(e.image_id) and by_image[e.image_id].presentation == "device"
            for e in scene.elements
        ):
            scene.notes += (
                "\nСкриншот оставлен без рамки: в шаблоне нет безопасной отделимой рамки."
            )
        return scene
    frames = [
        resource
        for resource in profile.resources
        if resource.kind == "device_frame"
        and resource.screen_box
        and Path(resource.preview_path).is_file()
    ]
    wrapped = False
    for image_element in list(scene.elements):
        uploaded = by_image.get(image_element.image_id)
        if uploaded is None or uploaded.presentation != "device":
            continue
        matches = sorted(
            (
                (
                    abs(
                        math.log(
                            (uploaded.width / uploaded.height)
                            / (frame.screen_box.w / frame.screen_box.h)
                        )
                    ),
                    frame,
                )
                for frame in frames
            ),
            key=lambda pair: pair[0],
        )
        if matches and matches[0][0] <= 0.2:
            frame = matches[0][1]
            outer = fit_box(frame.box, image_element.box)
            if min(outer.w, outer.h) < 50:
                scene.notes += (
                    "\nСкриншот оставлен без рамки: рамка слишком мала в свободной области."
                )
                continue
            screen = frame.screen_box
            inner = Box(
                x=outer.x + (screen.x - frame.box.x) * outer.w / frame.box.w,
                y=outer.y + (screen.y - frame.box.y) * outer.h / frame.box.h,
                w=screen.w * outer.w / frame.box.w,
                h=screen.h * outer.h / frame.box.h,
            )
            image_element.box = contained(uploaded, inner)
            scene.elements.append(
                Element(
                    kind="image",
                    box=outer,
                    image_path=frame.preview_path,
                    resource_id=frame.id,
                    role="template_resource",
                    text=frame.description,
                    field_style={"paired_image_id": uploaded.id},
                )
            )
            scene.notes += f"\nРамка из исходного шаблона: {frame.id}, слайд {frame.source_slide}."
            wrapped = True
            continue
        scene.notes += "\nСкриншот оставлен без рамки: нет безопасной рамки с подходящим экраном."
    if wrapped:
        return scene
    pattern = next((p for p in profile.patterns if p.id == scene.pattern_id), None)
    if pattern is None:
        return scene
    facts = {f.id: f.text for f in package.content.facts}
    content = " ".join([slide.title] + [facts.get(fid, "") for fid in slide.fact_ids]).casefold()
    words = set(re.findall(r"[^\W_]{4,}", content, re.UNICODE))
    matches = sorted(
        (
            (
                resource.confidence
                * len(
                    words
                    & {
                        word
                        for tag in resource.tags
                        for word in re.findall(r"[^\W_]{4,}", tag.casefold(), re.UNICODE)
                    }
                ),
                resource,
            )
            for resource in profile.resources
            if resource.kind == "icon"
            and resource.confidence >= 0.75
            and Path(resource.preview_path).is_file()
            and _resource_preview_matches_background(resource.preview_path, scene.background)
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    for score, resource in matches:
        if score <= 0:
            break
        for zone in pattern.body_zones:
            size = min(42, zone.w * 0.17, zone.h * 0.28)
            if size < 24:
                continue
            positions = (
                Box(x=zone.x + zone.w - size - 5, y=zone.y + 5, w=size, h=size),
                Box(x=zone.x + zone.w - size - 5, y=zone.y + zone.h - size - 5, w=size, h=size),
            )
            for candidate in positions:
                candidate = fit_box(resource.box, candidate)
                from studio.checks.audit import overlaps

                if any(
                    e.role != "template_background" and overlaps(candidate, e.box)
                    for e in scene.elements
                ):
                    continue
                scene.elements.append(
                    Element(
                        kind="image",
                        box=candidate,
                        image_path=resource.preview_path,
                        resource_id=resource.id,
                        role="template_resource",
                        text=resource.description,
                    )
                )
                scene.notes += (
                    f"\nИконка из исходного шаблона: {resource.id}, слайд {resource.source_slide}."
                )
                return scene
    return scene


def compose_images(slide, package, index, variant, images):
    from studio.composition.text_composer import text_element, fact_elements
    from studio.checks.audit import audit_scenes
    from studio.composition.contracts import (
        body_and_title_sources,
        candidates as semantic_candidates,
    )

    p = package.template
    facts = {f.id: f for f in package.content.facts}
    tables = {t.id: t for t in package.content.tables}
    relevant, title_ids = body_and_title_sources(slide, package.content)
    body = [f for f in relevant if f.source not in tables]
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
