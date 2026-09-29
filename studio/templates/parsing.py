from studio.templates.template_geometry import (
    luminance as luminance,
    contrast as contrast,
    color_value as color_value,
    walk_shapes as walk_shapes,
)
from collections import Counter, defaultdict
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
import colorsys
import json
import re
from PIL import Image
from studio.composition.powerpoint import open_presentation
from studio.composition.pictures import is_picture, embedded_picture_blob
from defusedxml import ElementTree as SafeET
from studio.models import TemplateProfile, Pattern, Asset
from studio.security import validate_pptx, scan_text, digest, InputRejected

EMU = 12700
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def analyze_template(
    path: Path, artifact_dir: Path, allow_download=False, font_progress=None
) -> TemplateProfile:
    warnings = validate_pptx(path)
    from studio.templates.colors import color_schemes, extract_colors

    color_roles, color_analysis = extract_colors(path, artifact_dir)
    prs = open_presentation(path)
    width, height = prs.slide_width / EMU, prs.slide_height / EMU
    if not (100 <= width <= 2500 and 100 <= height <= 2500):
        raise InputRejected("Неподдерживаемые размеры слайда")
    fonts, sizes, colors, text_colors = Counter(), Counter(), Counter(), Counter()
    theme_fonts, theme_colors = [], []
    with ZipFile(path) as z:
        for name in z.namelist():
            if re.fullmatch(r"ppt/theme/theme\d+\.xml", name):
                root = SafeET.fromstring(z.read(name))
                for node in root.iter(A + "latin"):
                    font = node.get("typeface", "")
                    if font and not font.startswith("+"):
                        theme_fonts.append(font)
                for scheme in root.iter(A + "clrScheme"):
                    for slot in scheme:
                        for node in slot:
                            c = node.get("lastClr") or node.get("val", "")
                            if re.fullmatch("[0-9a-fA-F]{6}", c):
                                theme_colors.append("#" + c.upper())
    patterns, counts, photos = [], Counter(), defaultdict(list)
    margins, title_sizes = [], []
    masters_layouts = [m for m in prs.slide_masters] + [
        layout for m in prs.slide_masters for layout in m.slide_layouts
    ]
    for idx, surface in enumerate(list(prs.slides) + masters_layouts):
        zones = []
        for shape, box in walk_shapes(surface.shapes):
            if idx < len(prs.slides):
                counts["objects"] += 1
                counts["placeholders"] += bool(shape.is_placeholder)
            if counts["objects"] > 30000:
                raise InputRejected("Превышен лимит 30 000 объектов")
            try:
                c = color_value(shape.fill.fore_color)
                if c:
                    colors[c] += max(1, box.w * box.h / 500)
            except (AttributeError, TypeError):
                pass
            if shape.has_text_frame:
                _, suspicious = scan_text(shape.text, "template")
                if suspicious:
                    warnings.append(
                        "Подозрительные инструкции внутри шаблона изолированы и исключены из модельного анализа"
                    )
                if (
                    shape.text.strip()
                    and box.w > 20
                    and box.h > 8
                    and 0 <= box.x < width
                    and 0 <= box.y < height
                ):
                    zones.append(box)
                    if 0.02 * width < box.x < 0.15 * width:
                        margins.append(box.x)
                for p in shape.text_frame.paragraphs:
                    for r in p.runs:
                        if r.font.name and not r.font.name.startswith("+"):
                            fonts[r.font.name] += max(1, len(r.text))
                        if r.font.size:
                            size = round(r.font.size.pt, 1)
                            sizes[size] += max(1, len(r.text))
                            if box.y < height * 0.22 and size >= 20:
                                title_sizes.append(size)
                        c = color_value(r.font.color)
                        if c:
                            colors[c] += max(1, len(r.text) / 5)
                            if idx < len(prs.slides):
                                text_colors[c] += max(1, len(r.text))
            if idx < len(prs.slides) and is_picture(shape):
                raw = embedded_picture_blob(shape)
                if raw is None:
                    warnings.append(
                        "Изображение без доступного встроенного содержимого пропущено; внешние ссылки не загружались"
                    )
                    continue
                key = digest(raw)
                # Small repeated assets at the canvas edge are candidate brand marks.
                if box.w * box.h < width * height * 0.04 and (
                    box.y < height * 0.15 or box.y + box.h > height * 0.85
                ):
                    photos[key].append((idx, box, raw))
        if idx < len(prs.slides) and zones:
            zones = sorted(zones, key=lambda b: (b.y, b.x))
            patterns.append(
                Pattern(
                    id=f"slide-{idx + 1}",
                    source_slide=idx + 1,
                    source_layout=surface.slide_layout.name,
                    text_zones=zones[:16],
                    role="columns"
                    if len(zones) >= 3
                    else "split"
                    if len(zones) == 2
                    else "statement",
                )
            )
    allowed_fonts = list(dict.fromkeys([f for f, _ in fonts.most_common()] + theme_fonts))
    if not allowed_fonts:
        raise InputRejected("В шаблоне не найден шрифт")
    from studio.templates.font_extraction import extract_template_fonts

    font_model = extract_template_fonts(
        path, artifact_dir, allow_download, font_progress, allowed_fonts[0]
    )
    warnings.extend(font_model["warnings"])
    primary = font_model["primary"]
    font = primary["requested"] if primary else allowed_fonts[0]
    font_file = primary["path"] if primary else ""
    font_origin = primary["origin"] if primary else {"kind": "missing"}
    allowed_fonts = list(
        dict.fromkeys(allowed_fonts + [a["requested"] for a in font_model["assets"]])
    )
    palette = list(dict.fromkeys([c for c, _ in colors.most_common(64)] + theme_colors))
    palette = list(dict.fromkeys(palette + [c for values in color_roles.values() for c in values]))
    palette = list(
        dict.fromkeys(
            palette
            + [
                paint["color"]
                for style in color_analysis.get("table_styles", {}).values()
                for paint in style.values()
            ]
        )
    )
    if not palette:
        raise InputRejected("В шаблоне не найдена палитра")
    paired = [
        scheme
        for scheme in color_analysis["schemes"]
        if scheme["background"] and scheme["foreground"]
    ]
    if paired:
        dominant = max(paired, key=lambda scheme: len(scheme["slides"]))
        background = dominant["background"]
        foreground = dominant["foreground"]
    else:
        primary_text = (
            text_colors.most_common(1)[0][0] if text_colors else min(palette, key=luminance)
        )
        background = max(palette, key=lambda c: contrast(c, primary_text))
        if color_roles.get("background"):
            background = color_roles["background"][0]
        foreground = max(palette, key=lambda c: contrast(c, background))
    saturated = [
        c
        for c in palette
        if colorsys.rgb_to_hsv(*[int(c[i : i + 2], 16) / 255 for i in (1, 3, 5)])[1] > 0.15
    ]
    accent = saturated[0] if saturated else foreground
    # PowerPoint often stores no size on an individual run. Empty POTX
    # placeholders inherit defRPr from their layout/master (and theme fonts).
    # Resolve the same chain used by native layout analysis, then prefer the
    # styles of shapes actually present on source slides.
    from studio.templates.native_style import native_styles

    styles = native_styles(path)
    effective_sizes, effective_titles, effective_bodies = Counter(), Counter(), Counter()
    for slide in prs.slides:
        part = str(slide.part.partname).lstrip("/")
        for shape, box in walk_shapes(slide.shapes):
            style = styles.get((part, shape.shape_id), {})
            size = style.get("size", 0)
            if not isinstance(size, (int, float)) or not 8 <= size <= 80:
                continue
            weight = max(1, len(shape.text)) if shape.has_text_frame else 1
            effective_sizes[size] += weight
            role = shape.placeholder_format.type.name if shape.is_placeholder else ""
            if role in ("TITLE", "CENTER_TITLE", "VERTICAL_TITLE"):
                effective_titles[size] += weight
            elif role in ("SUBTITLE", "BODY", "VERTICAL_BODY", "OBJECT"):
                effective_bodies[size] += weight
            elif shape.has_text_frame and shape.text.strip():
                (effective_titles if box.y < height * 0.22 else effective_bodies)[size] += weight
    if not effective_sizes:
        effective_sizes.update(
            style["size"]
            for (part, _), style in styles.items()
            if part.startswith("ppt/slideLayouts/") and 8 <= style.get("size", 0) <= 80
        )
    scale = sorted(s for s in sizes.keys() | effective_sizes.keys() if 8 <= s <= 80)
    if not scale:
        raise InputRejected("Не удалось извлечь типографическую шкалу")
    title_size = (
        effective_titles.most_common(1)[0][0]
        if effective_titles
        else min(scale, key=lambda s: abs(s - min(36, width * 0.038)))
    )
    body_size = (
        effective_bodies.most_common(1)[0][0]
        if effective_bodies
        else min(scale, key=lambda s: abs(s - min(20, width * 0.022)))
    )
    margin = sorted(margins)[len(margins) // 2] if margins else width * 0.05
    margin = max(width * 0.04, min(margin, width * 0.08))
    assets = []
    asset_dir = artifact_dir / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    for key, entries in photos.items():
        if len({entry[0] for entry in entries}) < 3:
            continue
        _, box, raw = entries[0]
        # A logo is only reused if repeated at essentially the same location.
        if any(abs(b.x - box.x) > 4 or abs(b.y - box.y) > 4 for _, b, _ in entries):
            continue
        try:
            with Image.open(BytesIO(raw)) as im:
                if im.width * im.height > 20_000_000:
                    continue
                if box.h <= 0 or abs((im.width / im.height) / (box.w / box.h) - 1) > 0.03:
                    # Cropped or masked source images need a dedicated crop adapter.
                    # Skip rather than stretch the full image into its source frame.
                    continue
                target = asset_dir / f"{key[:20]}.png"
                im.convert("RGBA").save(target)
            assets.append(
                Asset(
                    id=key[:20],
                    path=str(target),
                    box=box,
                    occurrences=len(entries),
                    role="brand_candidate",
                )
            )
        except (OSError, ValueError):
            continue
        if len(assets) == 2:
            break
    layouts = list(prs.slide_layouts)
    layout_index = min(range(len(layouts)), key=lambda i: len(layouts[i].shapes)) if layouts else 0
    ratio = counts["placeholders"] / max(counts["objects"], 1)
    from studio.templates.native_template import native_patterns

    native = native_patterns(prs, styles)
    if native:
        title_inks = {
            pattern.source_slide: pattern.title_foreground
            for pattern in native
            if pattern.source_slide and pattern.title_foreground
        }
        report = json.loads((artifact_dir / "color-model.json").read_text())
        color_analysis["schemes"], color_analysis["slide_schemes"] = color_schemes(
            report, title_inks
        )
        paired = [
            scheme
            for scheme in color_analysis["schemes"]
            if scheme["background"] and scheme["foreground"]
        ]
        if paired:
            dominant = max(paired, key=lambda scheme: len(scheme["slides"]))
            background, foreground = dominant["background"], dominant["foreground"]
    slide_schemes = color_analysis["slide_schemes"]
    for pattern in native or patterns:
        pattern.color_scheme_id = slide_schemes.get(str(pattern.source_slide), "")
    palette = list(
        dict.fromkeys(
            palette + [c for p in native for c in [p.title_foreground, *p.zone_foregrounds] if c]
        )
    )
    for pattern in native:
        pattern.table_style = color_analysis.get("table_styles", {}).get(
            str(pattern.source_slide), {}
        )
    if not native:
        warnings.append(
            "Не найден безопасный макет с заголовком и текстовыми зонами: используется композиция по токенам, сходство с шаблоном требует проверки."
        )
    return TemplateProfile(
        sha256=digest(path.read_bytes()),
        name=path.name,
        width=width,
        height=height,
        slide_count=len(prs.slides),
        master_count=len(prs.slide_masters),
        layout_count=sum(len(m.slide_layouts) for m in prs.slide_masters),
        object_count=counts["objects"],
        placeholder_count=counts["placeholders"],
        fonts=allowed_fonts,
        font=font,
        font_file=font_file,
        font_origin=font_origin,
        font_roles=font_model["roles"],
        font_assets=font_model["assets"],
        font_replacements=font_model["replacements"],
        missing_fonts=font_model["unresolved"],
        font_sizes=scale,
        title_size=title_size,
        body_size=body_size,
        colors=palette,
        background=background,
        foreground=foreground,
        accent=accent,
        margin=margin,
        color_roles=color_roles,
        color_schemes=color_analysis["schemes"],
        color_analysis=color_analysis,
        patterns=native or patterns,
        assets=assets,
        warnings=sorted(set(warnings)),
        source_kind="layout_rich" if ratio > 0.25 else "example_deck",
        layout_index=layout_index,
        analysis_version=11,
    )
