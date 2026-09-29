"""Shared shape IDs, paint evidence, and text color inheritance."""

from __future__ import annotations

from xml.etree import ElementTree as ET

from studio._vendor.color_extraction.pipeline.reference_color_values import paint_colors, resolve_color
from studio._vendor.color_extraction.pipeline.reference_font_usage_styles import _source_properties

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
COLOR_TAGS = {"srgbClr", "schemeClr", "sysClr", "prstClr", "scrgbClr", "hslClr"}
PAINT_TAGS = {"solidFill", "gradFill", "pattFill", "blipFill", "grpFill", "noFill"}


def _id(shape: ET.Element, source: str) -> str:
    node = shape.find(f"{{{P}}}nvSpPr/{{{P}}}cNvPr")
    if node is None:
        node = shape.find(f"{{{P}}}nvGraphicFramePr/{{{P}}}cNvPr")
    if node is None:
        node = shape.find(f"{{{P}}}nvCxnSpPr/{{{P}}}cNvPr")
    if node is None:
        node = shape.find(f"{{{P}}}nvPicPr/{{{P}}}cNvPr")
    return f"{source}:{node.get('id', '?') if node is not None else '?'}"


def _first_paint(node: ET.Element | None) -> ET.Element | None:
    if node is None:
        return None
    return next((child for child in node if child.tag.rsplit("}", 1)[-1] in PAINT_TAGS), None)


def _style_paint(
    shape: ET.Element,
    role: str,
    theme: ET.Element | None,
    palette: dict[str, str],
    color_map: dict[str, str],
) -> tuple[list[dict], str | None] | None:
    ref_name = {"fill": "fillRef", "line": "lnRef", "text": "fontRef"}[role]
    reference = shape.find(f"{{{P}}}style/{{{A}}}{ref_name}")
    if reference is None:
        return None
    token = next((child for child in reference if child.tag.rsplit("}", 1)[-1] in COLOR_TAGS), None)
    placeholder = resolve_color(token, palette, color_map) if token is not None else None
    if role == "text":
        return ([placeholder], None) if placeholder else ([], "unresolved-fontRef")
    if theme is None:
        return [], "missing-theme-style"
    try:
        index = int(reference.get("idx", "0"))
    except ValueError:
        return [], "invalid-style-index"
    if index == 0:
        return [], None
    group = theme.find(
        f".//{{{A}}}fmtScheme/{{{A}}}{'fillStyleLst' if role == 'fill' else 'lnStyleLst'}"
    )
    if index >= 1001 and role == "fill":
        group = theme.find(f".//{{{A}}}fmtScheme/{{{A}}}bgFillStyleLst")
        index -= 1000
    if group is None or index < 1 or index > len(group):
        return [], "missing-theme-style"
    return paint_colors(
        group[index - 1], palette, color_map, placeholder["hex"] if placeholder else None
    )


def _record(
    uses: list[dict],
    unresolved: list[dict],
    shape_id: str,
    role: str,
    source: str,
    colors: list[dict],
    reason: str | None,
) -> None:
    for index, color in enumerate(colors):
        if color["opacity"] == 0:
            continue
        uses.append(
            {
                "shapeId": shape_id,
                "role": role,
                "color": color["hex"],
                "opacity": color["opacity"],
                "source": source,
                "token": color["token"],
                "tokenValue": color["value"],
                **({"stop": index, "position": color["position"]} if "position" in color else {}),
                **({"component": color["component"]} if "component" in color else {}),
            }
        )
    if reason:
        unresolved.append({"shapeId": shape_id, "role": role, "source": source, "reason": reason})


def _text_color(
    paragraph: ET.Element,
    run: ET.Element,
    ancestors: list[tuple[str, ET.Element]],
    master: ET.Element | None,
    presentation: ET.Element,
    role: str,
    source: str,
    theme: ET.Element | None,
    palette: dict[str, str],
    color_map: dict[str, str],
) -> tuple[list[dict], str, str | None]:
    properties = _source_properties(paragraph, run, ancestors, master, presentation, role, source)
    for origin, node in properties:
        paint = _first_paint(node)
        if paint is not None:
            colors, reason = paint_colors(paint, palette, color_map)
            return colors, origin, reason
    for origin, shape in ancestors:
        styled = _style_paint(shape, "text", theme, palette, color_map)
        if styled is not None:
            return styled[0], f"{origin}-style", styled[1]
    default = palette.get(color_map.get("tx1", "dk1"))
    if default:
        return (
            [{"hex": default, "opacity": 1.0, "token": "schemeClr", "value": "tx1"}],
            "theme-default",
            None,
        )
    return [], "theme-default", "missing-text-color"


def _background(
    roots: list[ET.Element],
    theme: ET.Element | None,
    palette: dict[str, str],
    color_map: dict[str, str],
    uses: list[dict],
    unresolved: list[dict],
) -> None:
    for origin, root in zip(("slide", "layout", "master"), roots, strict=False):
        background = root.find(f"{{{P}}}cSld/{{{P}}}bg")
        if background is None:
            continue
        properties = background.find(f"{{{P}}}bgPr")
        if properties is not None:
            paint = _first_paint(properties)
            if paint is not None:
                _record(
                    uses,
                    unresolved,
                    "canvas",
                    "background",
                    origin,
                    *paint_colors(paint, palette, color_map),
                )
                return
        reference = background.find(f"{{{P}}}bgRef")
        if reference is not None:
            token = next(
                (child for child in reference if child.tag.rsplit("}", 1)[-1] in COLOR_TAGS), None
            )
            color = resolve_color(token, palette, color_map) if token is not None else None
            group = (
                theme.find(f".//{{{A}}}fmtScheme/{{{A}}}bgFillStyleLst")
                if theme is not None
                else None
            )
            try:
                index = int(reference.get("idx", "0")) - 1001
            except ValueError:
                index = -1
            if group is not None and 0 <= index < len(group):
                values, reason = paint_colors(
                    group[index], palette, color_map, color["hex"] if color else None
                )
            else:
                values, reason = [], "missing-background-style"
            _record(uses, unresolved, "canvas", "background", f"{origin}-style", values, reason)
            return
    default = palette.get(color_map.get("bg1", "lt1"))
    _record(
        uses,
        unresolved,
        "canvas",
        "background",
        "theme-default",
        [{"hex": default, "opacity": 1.0, "token": "schemeClr", "value": "bg1"}] if default else [],
        None if default else "missing-background",
    )
