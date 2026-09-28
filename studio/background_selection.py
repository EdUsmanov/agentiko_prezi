"""Source artwork for generic geometry, admitted only on sanitized empty regions."""

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops


@lru_cache(maxsize=64)
def _image(path, modified, size):
    with Image.open(path) as image:
        return image.convert("RGB")


def flat_region(path, box, width, height):
    if (
        not path
        or not Path(path).is_file()
        or box.x < 0
        or box.y < 0
        or box.x + box.w > width + 0.5
        or box.y + box.h > height + 0.5
    ):
        return None
    stat = Path(path).stat()
    image = _image(str(path), stat.st_mtime_ns, stat.st_size)
    crop = image.crop(
        (
            round(box.x / width * image.width),
            round(box.y / height * image.height),
            round((box.x + box.w) / width * image.width),
            round((box.y + box.h) / height * image.height),
        )
    )
    if crop.width < 1 or crop.height < 1:
        return None
    colors = crop.getcolors(crop.width * crop.height)
    background = max(colors, key=lambda row: row[0])[1]
    hist = (
        ImageChops.difference(crop, Image.new("RGB", crop.size, background))
        .convert("L")
        .histogram()
    )
    if sum(hist[:10]) / sum(hist) < 0.995:
        return None
    return "#%02X%02X%02X" % background


def background_candidates(package):
    profile = package.template
    if (
        not getattr(profile, "background_source", "")
        or not Path(profile.background_source).is_file()
    ):
        return []
    return [
        p
        for p in profile.patterns
        if p.reusable
        and p.purpose not in ("cover", "divider", "service", "reference")
        and p.graphic_kind == "none"
        and p.background_image
        and Path(p.background_image).is_file()
    ]


def apply_background(scene, package, pattern_id):
    from .models import Box, Element
    from .template_geometry import contrast, minimum_text_contrast
    from .table_style import cell_paint

    if (
        scene.pattern_id
        or scene.purpose in ("cover", "divider")
        or scene.strategy != "token_composition"
        or any(e.image_id for e in scene.elements)
    ):
        return None
    pattern = next((p for p in background_candidates(package) if p.id == pattern_id), None)
    if pattern is None:
        return None
    profile = package.template
    result = scene.model_copy(deep=True)
    result.background = pattern.background or scene.background
    result.background_pattern_id = pattern.id
    result.elements = [e for e in result.elements if e.role != "template_background"]
    colors = list(
        dict.fromkeys(
            c
            for c in [
                pattern.foreground,
                pattern.title_foreground,
                profile.foreground,
                *profile.colors,
            ]
            if c
        )
    )
    for e in result.elements:
        background = flat_region(pattern.background_image, e.box, profile.width, profile.height)
        if background is None:
            return None
        e.background_hint = background
        if e.kind in ("text", "chart", "table"):
            required = minimum_text_contrast(e.size, e.bold) if e.kind == "text" else 4.5
            candidates = [
                c for c in [e.color, *colors] if c and contrast(c, background) >= required
            ]
            if not candidates:
                return None
            e.color = candidates[0]
            if e.kind == "table":
                for row in (0, 1):
                    fill, opacity, ink = cell_paint(e, row, result, profile)
                    painted = background
                    if opacity:
                        painted = "#%02X%02X%02X" % tuple(
                            round(
                                int(fill[i : i + 2], 16) * opacity
                                + int(background[i : i + 2], 16) * (1 - opacity)
                            )
                            for i in (1, 3, 5)
                        )
                    if contrast(ink, painted) < 4.5:
                        return None
        elif e.kind in ("line", "rect"):
            candidates = [
                c for c in [e.fill or e.color, *colors] if c and contrast(c, background) >= 3
            ]
            if not candidates:
                return None
            if e.fill:
                e.fill = candidates[0]
            else:
                e.color = candidates[0]
    result.elements.insert(
        0,
        Element(
            kind="image",
            box=Box(x=0, y=0, w=profile.width, h=profile.height),
            image_path=pattern.background_image,
            role="template_background",
        ),
    )
    return result


def master_fallback(variant, package, index, scene, image_groups, compose):
    """Authored layouts keep priority; safe masters precede a generic fallback."""
    from .contracts import candidates
    from .audit import audit_scenes, repair_scenes
    from .quality import candidate_regressions
    from .repair_policy import FIT_CODES

    baseline = scene.model_copy(deep=True)
    repair_scenes([baseline], package)
    if scene.pattern_id and not any(f.code in FIT_CODES for f in audit_scenes([baseline], package)):
        return None
    if any(e.image_id for e in scene.elements):
        return None
    for pattern in candidates(package, variant.slides[index], index, prefer_specialized=False):
        if pattern.source_slide:
            continue
        trial = variant.model_copy(deep=True)
        trial.slides[index].pattern_id = pattern.id
        trial.slides[index].background_pattern_id = None
        try:
            result = compose(trial, package, index, image_groups)
            repair_scenes([result], package)
        except ValueError:
            continue
        if result.pattern_id != pattern.id or any(
            f.code in FIT_CODES for f in audit_scenes([result], package)
        ):
            continue
        if preserves_data_space(baseline, result) and not candidate_regressions(
            [baseline], [result], package
        ):
            return result
    return None


def background_is_safe(scene, package):
    """Recheck actual geometry and paint after downstream layout edits."""
    if not scene.background_pattern_id:
        return True
    candidate = apply_background(scene, package, scene.background_pattern_id)
    return candidate is not None and candidate == scene


def preserves_data_space(before, after):
    """Background variety must not reduce either dimension of an evidence object."""
    old = {
        (e.kind, tuple(e.source_ids)): e.box
        for e in before.elements
        if e.kind in ("chart", "table")
    }
    new = {
        (e.kind, tuple(e.source_ids)): e.box for e in after.elements if e.kind in ("chart", "table")
    }
    return all(
        key in new and new[key].w >= box.w - 0.5 and new[key].h >= box.h - 0.5
        for key, box in old.items()
    )
