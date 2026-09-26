"""Resolve the color of displayed OOXML bullets through paragraph inheritance."""

from __future__ import annotations

from xml.etree import ElementTree as ET

from .reference_color_values import resolve_color
from .reference_font_usage_styles import _placeholder

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
COLOR_TAGS = {"srgbClr", "schemeClr", "sysClr", "prstClr", "scrgbClr", "hslClr"}
BULLET_TAGS = {"buNone", "buChar", "buAutoNum", "buBlip"}


def bullet_color(
    paragraph: ET.Element,
    ancestors: list[tuple[str, ET.Element]],
    master: ET.Element | None,
    presentation: ET.Element,
    role: str,
    source: str,
    palette: dict[str, str],
    color_map: dict[str, str],
) -> tuple[list[dict], str, str | None] | None:
    """Return a distinct bullet color only when a bullet is actually enabled."""
    candidates: list[tuple[str, ET.Element]] = []
    paragraph_style = paragraph.find(f"{{{A}}}pPr")
    level = (
        min(9, max(1, int(paragraph_style.get("lvl", "0")) + 1))
        if paragraph_style is not None
        else 1
    )
    if paragraph_style is not None:
        candidates.append((source, paragraph_style))
    for origin, shape in ancestors:
        body = shape.find(f"{{{P}}}txBody")
        if body is None:
            continue
        if origin != source:
            sample = body.find(f"{{{A}}}p/{{{A}}}pPr")
            if sample is not None:
                candidates.append((origin, sample))
        style = body.find(f"{{{A}}}lstStyle/{{{A}}}lvl{level}pPr")
        if style is not None:
            candidates.append((origin, style))
    if master is not None:
        kind = (
            "titleStyle"
            if role == "title"
            else "bodyStyle"
            if ancestors and _placeholder(ancestors[0][1])
            else "otherStyle"
        )
        style = master.find(f"{{{P}}}txStyles/{{{P}}}{kind}/{{{A}}}lvl{level}pPr")
        if style is not None:
            candidates.append(("master", style))
    default = presentation.find(f"{{{P}}}defaultTextStyle/{{{A}}}lvl{level}pPr")
    if default is not None:
        candidates.append(("presentation", default))
    bullet = next(
        (
            child.tag.rsplit("}", 1)[-1]
            for _, style in candidates
            for child in style
            if child.tag.rsplit("}", 1)[-1] in BULLET_TAGS
        ),
        None,
    )
    if bullet is None or bullet == "buNone":
        return None
    for origin, style in candidates:
        wrapper = style.find(f"{{{A}}}buClr")
        if wrapper is None:
            continue
        token = next(
            (child for child in wrapper if child.tag.rsplit("}", 1)[-1] in COLOR_TAGS), None
        )
        resolved = resolve_color(token, palette, color_map) if token is not None else None
        return (
            [resolved] if resolved else [],
            origin,
            None if resolved else "unresolved-bullet-color",
        )
    return None
