"""Bounded native icon and empty-device-frame catalogue from an uploaded PPTX."""

from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageOps
from pydantic import Field

from studio.models import Box, StrictModel, TemplateResource
from studio.composition.pictures import A, P, R, embedded_blip_blob, is_picture
from studio.composition.powerpoint import open_presentation
from studio.security import digest, scan_text
from studio.composition.shape_geometry import intersects
from studio.templates.template_geometry import walk_shapes


class ResourceChoice(StrictModel):
    id: str
    kind: Literal["skip", "icon", "device_frame"]
    description: str = Field(default="", max_length=160)
    tags: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(default=0, ge=0, le=1)
    screen_box: list[int] | None = None  # 0..1000, relative to the candidate crop.


class ResourceChoices(StrictModel):
    choices: list[ResourceChoice] = Field(default_factory=list, max_length=32)


def _local_nodes(shape):
    for node in shape._element.iter():
        for attr, rid in node.attrib.items():
            if attr.startswith(R):
                rel = shape.part.rels.get(rid)
                if rel is None or rel.is_external or not rel.reltype.endswith("/image"):
                    return False
    return True


def _empty_artwork(shape):
    if shape._element.tag not in (P + "sp", P + "grpSp", P + "pic"):
        return False
    if any((node.text or "").strip() for node in shape._element.iter(A + "t")):
        return False
    if not _local_nodes(shape):
        return False
    for node in shape._element.iter(A + "blip"):
        if embedded_blip_blob(node, shape.part) is None:
            return False
    return True


def _box_inside(outer, inner, margin=1):
    return (
        inner.x >= outer.x + margin
        and inner.y >= outer.y + margin
        and inner.x + inner.w <= outer.x + outer.w - margin
        and inner.y + inner.h <= outer.y + outer.h - margin
    )


def _screen_is_empty(shape, screen):
    # A group can be layered over a screenshot only if it has an open hole.
    if shape._element.tag != P + "grpSp" or not _box_inside(_shape_box(shape), screen, 2):
        return False
    for child, child_box in list(walk_shapes([shape]))[1:]:
        if not intersects(child_box, screen):
            continue
        overlap = max(
            0, min(child_box.x + child_box.w, screen.x + screen.w) - max(child_box.x, screen.x)
        ) * max(0, min(child_box.y + child_box.h, screen.y + screen.h) - max(child_box.y, screen.y))
        if overlap < screen.w * screen.h * 0.04:
            continue
        if is_picture(child):
            return False
        try:
            if child.fill.type is not None and overlap > screen.w * screen.h * 0.15:
                return False
        except (AttributeError, ValueError):
            pass
    return True


def _shape_box(shape):
    from studio.templates.template_geometry import EMU

    return Box(x=shape.left / EMU, y=shape.top / EMU, w=shape.width / EMU, h=shape.height / EMU)


def _transparent_icon(blob):
    try:
        with Image.open(BytesIO(blob)) as image:
            if "A" not in image.getbands():
                return False
            alpha = image.getchannel("A")
            w, h = alpha.size
            border = list(alpha.crop((0, 0, w, 1)).getdata())
            border += list(alpha.crop((0, h - 1, w, h)).getdata())
            border += list(alpha.crop((0, 0, 1, h)).getdata())
            border += list(alpha.crop((w - 1, 0, w, h)).getdata())
            return sum(value < 32 for value in border) >= len(border) // 4
    except (OSError, ValueError):
        return False


def _old_content_overlaps(slide, selected, box):
    surfaces = (slide, slide.slide_layout, slide.slide_layout.slide_master)
    for surface in surfaces:
        background = surface._element.find(P + "cSld/" + P + "bg")
        if background is not None and any(True for _ in background.iter(A + "blip")):
            return True
    for surface in surfaces:
        for other in surface.shapes:
            if other is selected or surface is not slide and other.is_placeholder:
                continue
            other_box = _shape_box(other)
            overlap = max(
                0, min(box.x + box.w, other_box.x + other_box.w) - max(box.x, other_box.x)
            ) * max(0, min(box.y + box.h, other_box.y + other_box.h) - max(box.y, other_box.y))
            if overlap < box.w * box.h * 0.02:
                continue
            has_text = any((node.text or "").strip() for node in other._element.iter(A + "t"))
            has_image = any(True for _ in other._element.iter(A + "blip"))
            if has_text or has_image:
                return True
    return False


def _candidate_shapes(path, profile):
    prs = open_presentation(path)
    eligible = {p.source_slide for p in profile.patterns if p.source_slide}
    brands = {a.id for a in profile.assets}
    seen = set()
    result = []
    for number in sorted(eligible):
        slide = prs.slides[number - 1]
        for shape in slide.shapes:
            box = _shape_box(shape)
            if not _empty_artwork(shape) or box.w <= 4 or box.h <= 4:
                continue
            if (
                box.x < 0
                or box.y < 0
                or box.x + box.w > profile.width
                or box.y + box.h > profile.height
            ):
                continue
            relative_w, relative_h = box.w / profile.width, box.h / profile.height
            icon = 0.015 <= relative_w <= 0.20 and 0.015 <= relative_h <= 0.25
            frame = (
                shape._element.tag == P + "grpSp"
                and 0.16 <= relative_w <= 0.75
                and 0.18 <= relative_h <= 0.85
                and len(shape.shapes) >= 2
            )
            if not (icon or frame):
                continue
            if (
                icon
                and shape._element.tag == P + "grpSp"
                and any(is_picture(child) for child, _ in list(walk_shapes([shape]))[1:])
            ):
                icon = False
                if not frame:
                    continue
            if _old_content_overlaps(slide, shape, box):
                continue
            if is_picture(shape):
                from studio.composition.pictures import embedded_picture_blob

                raw = embedded_picture_blob(shape)
                if raw is None or digest(raw)[:20] in brands or not _transparent_icon(raw):
                    continue
            # Similar repeated edge artwork is part of the brand, not a reusable icon.
            edge = box.x < profile.width * 0.04 or box.y < profile.height * 0.04
            edge |= box.x + box.w > profile.width * 0.96 or box.y + box.h > profile.height * 0.96
            if edge:
                continue
            key = (number, shape.shape_id)
            if key in seen:
                continue
            seen.add(key)
            result.append(
                (
                    f"resource-{number}-{shape.shape_id}",
                    number,
                    shape.shape_id,
                    box,
                    shape,
                    icon,
                    frame,
                )
            )
            if len(result) >= 32:
                return result
    return result


def _preview(source, box, profile):
    with Image.open(BytesIO(source)) as image:
        x0 = max(0, int(box.x / profile.width * image.width))
        y0 = max(0, int(box.y / profile.height * image.height))
        x1 = min(image.width, int((box.x + box.w) / profile.width * image.width))
        y1 = min(image.height, int((box.y + box.h) / profile.height * image.height))
        crop = image.crop((x0, y0, x1, y1)).convert("RGB")
    crop.thumbnail((360, 240), Image.Resampling.LANCZOS)
    return crop


def _montage(rows):
    canvas = Image.new("RGB", (1200, 160 * ((len(rows) + 5) // 6)), "#f4f4f4")
    draw = ImageDraw.Draw(canvas)
    for index, (identifier, image) in enumerate(rows):
        x, y = (index % 6) * 200, (index // 6) * 160
        tile = ImageOps.contain(image, (188, 126))
        canvas.paste(tile, (x + (200 - tile.width) // 2, y + 4))
        draw.text((x + 5, y + 133), identifier, fill="#111111")
    output = BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue()


async def discover_resources(path: Path, profile, gateway, images=None, render_source_images=None):
    """Return typed references only; never modify or execute the source deck."""
    if gateway.settings.mode != "api":
        return [], {"status": "not_run", "reason": "model_unavailable"}
    candidates = _candidate_shapes(path, profile)
    if not candidates:
        return [], {"status": "completed", "candidates": 0, "selected": 0}
    if not images:
        images = render_source_images(path, profile, path.parent) if render_source_images else {}
    folder = path.parent / "template-resources"
    folder.mkdir(exist_ok=True)
    previews = []
    available = []
    for identifier, number, shape_id, box, shape, icon, frame in candidates:
        if number not in images:
            continue
        try:
            preview = _preview(images[number], box, profile)
        except (OSError, ValueError):
            continue
        target = folder / f"{identifier}.png"
        preview.save(target)
        previews.append((identifier, preview))
        available.append((identifier, number, shape_id, box, shape, icon, frame, target))
    if not available:
        return [], {"status": "not_run", "reason": "source_preview_unavailable"}
    choices = ResourceChoices.model_validate(
        await gateway.json_request(
            "template_resources",
            {
                "candidates": [
                    {"id": identifier, "may_be_icon": icon, "may_be_frame": frame}
                    for identifier, _, _, _, _, icon, frame, _ in available
                ]
            },
            timeout=120,
            schema=ResourceChoices.model_json_schema(),
            images=[_montage(previews)],
        )
    ).choices
    by_id = {row[0]: row for row in available}
    if len({choice.id for choice in choices}) != len(choices) or {
        choice.id for choice in choices
    } != set(by_id):
        raise ValueError("Модель вернула неизвестный или повторный ресурс")
    resources = []
    for choice in choices:
        identifier, number, shape_id, box, shape, icon, frame, target = by_id[choice.id]
        if choice.confidence < 0.75 or choice.kind not in ("icon", "device_frame"):
            continue
        if choice.kind == "icon" and not icon or choice.kind == "device_frame" and not frame:
            continue
        tags = []
        for tag in choice.tags:
            clean, suspicious = scan_text(tag, "template")
            if suspicious or not clean.strip() or len(clean) > 50:
                continue
            tags.append(clean.strip().casefold())
        description, suspicious = scan_text(choice.description, "template")
        if suspicious:
            continue
        screen = None
        if choice.kind == "device_frame":
            raw = choice.screen_box
            if (
                raw is None
                or len(raw) != 4
                or any(type(v) is not int or not 0 <= v <= 1000 for v in raw)
            ):
                continue
            x0, y0, x1, y1 = raw
            if x1 <= x0 or y1 <= y0:
                continue
            screen = Box(
                x=box.x + box.w * x0 / 1000,
                y=box.y + box.h * y0 / 1000,
                w=box.w * (x1 - x0) / 1000,
                h=box.h * (y1 - y0) / 1000,
            )
            if screen.w < 20 or screen.h < 20 or not _screen_is_empty(shape, screen):
                continue
            with Image.open(target) as opaque:
                cutout = opaque.convert("RGBA")
            ImageDraw.Draw(cutout).rectangle(
                (
                    round(x0 / 1000 * cutout.width),
                    round(y0 / 1000 * cutout.height),
                    round(x1 / 1000 * cutout.width),
                    round(y1 / 1000 * cutout.height),
                ),
                fill=(0, 0, 0, 0),
            )
            cutout.save(target)
        resources.append(
            TemplateResource(
                id=identifier,
                kind=choice.kind,
                source_slide=number,
                shape_ids=[shape_id],
                box=box,
                preview_path=str(target.resolve()),
                description=description.strip(),
                tags=list(dict.fromkeys(tags)),
                confidence=choice.confidence,
                screen_box=screen,
            )
        )
    return resources, {
        "status": "completed",
        "candidates": len(available),
        "selected": len(resources),
    }
