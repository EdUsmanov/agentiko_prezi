"""Conservative safe-area inference from already rendered, text-free artwork."""

from io import BytesIO
from statistics import median
from PIL import Image
from .models import Box


def safe_body_zone(image, zone, scale=1.5):
    # A uniform/tinted area is safer than decorative strokes. This is a
    # heuristic, not a proof; final native-render visual QA still runs.
    columns, rows = 16, 20

    def pixel(x, y):
        return image.getpixel(
            (
                max(0, min(image.width - 1, int(x * scale))),
                max(0, min(image.height - 1, int(y * scale))),
            )
        )[:3]

    samples = [
        pixel(zone.x + zone.w * (x + 0.5) / columns, zone.y + zone.h * (y + 0.5) / rows)
        for y in range(rows)
        for x in range(columns)
    ]
    base = tuple(median(p[c] for p in samples) for c in range(3))
    safe = []
    for y in range(rows):
        line = []
        for x in range(columns):
            points = [
                pixel(zone.x + zone.w * (x + dx) / columns, zone.y + zone.h * (y + dy) / rows)
                for dx, dy in ((0.15, 0.15), (0.85, 0.15), (0.5, 0.5), (0.15, 0.85), (0.85, 0.85))
            ]
            line.append(all(sum((p[c] - base[c]) ** 2 for c in range(3)) < 65**2 for p in points))
        safe.append(line)
    best = None
    for top in range(rows):
        valid = [True] * columns
        for bottom in range(top, rows):
            valid = [a and b for a, b in zip(valid, safe[bottom])]
            start = 0
            for end in range(columns + 1):
                if end < columns and valid[end]:
                    continue
                width = end - start
                height = bottom - top + 1
                if width >= columns * 0.55 and height >= rows * 0.3:
                    score = width * height
                    if best is None or score > best[0]:
                        best = (score, start, top, width, height)
                start = end + 1
    if best is None or best[0] < columns * rows * 0.3 or best[0] == columns * rows:
        return zone
    _, x, y, w, h = best
    return Box(
        x=zone.x + zone.w * x / columns,
        y=zone.y + zone.h * y / rows,
        w=zone.w * w / columns,
        h=zone.h * h / rows,
    )


def constrain_body_zones(pattern, image, scale=1.5):
    """Intersect each authored field with its own rendered clear area.

    The flattened, text-free background includes inherited master/layout art
    and grouped vectors that inspecting slide pictures alone cannot see. Never
    merge cards, expand a field, move its source shape or modify the artwork.
    Both engines consume the same updated zones and source-object contracts.
    """
    adjustments = []
    for index, original in enumerate(pattern.body_zones):
        safe = safe_body_zone(image, original, scale)
        if safe == original:
            continue
        adjustments.append(
            {
                "index": index,
                "before": original.model_dump(),
                "after": safe.model_dump(),
                "reason": "rendered_artwork_clear_area",
            }
        )
        pattern.body_zones[index] = safe
    pattern.text_zones = [pattern.title_zone] + pattern.body_zones
    for field in pattern.fields:
        if field["role"] == "body":
            field["box"] = pattern.body_zones[field["index"]].model_dump()
    pattern.safe_text_zone["body_adjustments"] = adjustments
    return adjustments


def picture_safe_body_zone(surface, zone, width, height, scale=1.5):
    """Exclude visible artwork embedded inside a source text field.

    PPTAgent preserves pictures from an exemplar as template artwork, while the
    normal sanitized background deliberately omits one-off pictures.  Inspect
    those pixels locally so a large photo/illustration cannot remain hidden
    inside the field's nominal rectangle.  The pixels are not persisted or sent
    to a model.
    """
    from .pictures import embedded_picture_blob, is_picture
    from .native_template import box

    canvas = Image.new(
        "RGB", (max(1, round(width * scale)), max(1, round(height * scale))), "white"
    )
    found = False
    for shape in surface.shapes:
        if not is_picture(shape):
            continue
        bounds = box(shape)
        overlap_w = min(zone.x + zone.w, bounds.x + bounds.w) - max(zone.x, bounds.x)
        overlap_h = min(zone.y + zone.h, bounds.y + bounds.h) - max(zone.y, bounds.y)
        if overlap_w <= 2 or overlap_h <= 2:
            continue
        raw = embedded_picture_blob(shape)
        if raw is None:
            continue
        try:
            with Image.open(BytesIO(raw)) as source:
                source = source.convert("RGB")
                left = round(source.width * getattr(shape, "crop_left", 0))
                top = round(source.height * getattr(shape, "crop_top", 0))
                right = round(source.width * (1 - getattr(shape, "crop_right", 0)))
                bottom = round(source.height * (1 - getattr(shape, "crop_bottom", 0)))
                source = source.crop((left, top, max(left + 1, right), max(top + 1, bottom)))
                source = source.resize(
                    (max(1, round(bounds.w * scale)), max(1, round(bounds.h * scale)))
                )
                canvas.paste(source, (round(bounds.x * scale), round(bounds.y * scale)))
                found = True
        except (OSError, ValueError):
            # Unsupported image encodings remain covered by final rendered QA.
            continue
    return safe_body_zone(canvas, zone, scale) if found else zone
