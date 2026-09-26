"""Resolve DrawingML color tokens to RGB and opacity without palette guesses."""

from __future__ import annotations

import colorsys
import re
from xml.etree import ElementTree as ET

from PIL import ImageColor

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
COLOR_TAGS = {"srgbClr", "schemeClr", "sysClr", "prstClr", "scrgbClr", "hslClr"}
PRESET_COLORS = {"black": "#000000", "white": "#FFFFFF"}
TRANSFORMS = {
    "alpha",
    "alphaMod",
    "alphaOff",
    "tint",
    "shade",
    "lumMod",
    "lumOff",
    "satMod",
    "satOff",
    "hueMod",
    "hueOff",
    "redMod",
    "redOff",
    "greenMod",
    "greenOff",
    "blueMod",
    "blueOff",
}


def theme_colors(theme: ET.Element | None) -> dict[str, str]:
    """Read raw scheme colors; slide/layout/master clrMap is applied at use time."""
    if theme is None:
        return {}
    scheme = theme.find(f".//{{{A}}}clrScheme")
    if scheme is None:
        return {}
    result = {}
    for item in scheme:
        token = next((child for child in item if child.tag.rsplit("}", 1)[-1] in COLOR_TAGS), None)
        if token is not None:
            resolved = resolve_color(token, {}, {})
            if resolved:
                result[item.tag.rsplit("}", 1)[-1]] = resolved["hex"]
    return result


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{min(255, max(0, int(value * 255 + 0.5))):02X}" for value in rgb)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _preset_color(name: str) -> str | None:
    if name in PRESET_COLORS:
        return PRESET_COLORS[name]
    expanded = re.sub(
        r"^(dk|lt|med)(?=[A-Z])",
        lambda match: {"dk": "dark", "lt": "light", "med": "medium"}[match.group()],
        name,
    )
    try:
        return _hex(tuple(channel / 255 for channel in ImageColor.getrgb(expanded)))
    except ValueError:
        return None


def resolve_color(
    token: ET.Element,
    palette: dict[str, str],
    color_map: dict[str, str],
    placeholder: str | None = None,
) -> dict | None:
    """Return a concrete RGB/alpha token or None when OOXML cannot resolve it."""
    kind = token.tag.rsplit("}", 1)[-1]
    raw = token.get("val", "")
    if kind == "srgbClr" and re.fullmatch(r"[0-9A-Fa-f]{6}", raw):
        base = f"#{raw.upper()}"
    elif kind == "sysClr" and re.fullmatch(r"[0-9A-Fa-f]{6}", token.get("lastClr", "")):
        base = f"#{token.get('lastClr').upper()}"
    elif kind == "prstClr":
        base = _preset_color(raw)
    elif kind == "schemeClr":
        key = color_map.get(raw, raw)
        base = placeholder if raw == "phClr" else palette.get(key)
    elif kind == "scrgbClr":
        try:
            base = _hex(tuple(_clamp(int(token.get(key, "0")) / 100000) for key in ("r", "g", "b")))
        except ValueError:
            return None
    elif kind == "hslClr":
        try:
            base = _hex(
                colorsys.hls_to_rgb(
                    (int(token.get("hue", "0")) / 21600000) % 1,
                    _clamp(int(token.get("lum", "0")) / 100000),
                    _clamp(int(token.get("sat", "0")) / 100000),
                )
            )
        except ValueError:
            return None
    else:
        return None
    if not base:
        return None
    rgb = tuple(int(base[index : index + 2], 16) / 255 for index in (1, 3, 5))
    alpha = 1.0
    for transform in token:
        operation = transform.tag.rsplit("}", 1)[-1]
        if operation not in TRANSFORMS:
            return None
        try:
            amount = int(transform.get("val", "0")) / 100000
        except ValueError:
            return None
        if operation == "alpha":
            alpha = amount
        elif operation == "alphaMod":
            alpha *= amount
        elif operation == "alphaOff":
            alpha += amount
        elif operation == "tint":
            rgb = tuple(channel + (1 - channel) * amount for channel in rgb)
        elif operation == "shade":
            rgb = tuple(channel * amount for channel in rgb)
        elif operation in {"redMod", "greenMod", "blueMod", "redOff", "greenOff", "blueOff"}:
            index = {"red": 0, "green": 1, "blue": 2}[operation[:-3]]
            values = list(rgb)
            values[index] = _clamp(
                values[index] * amount if operation.endswith("Mod") else values[index] + amount
            )
            rgb = tuple(values)
        else:
            hue, lightness, saturation = colorsys.rgb_to_hls(*rgb)
            if operation == "lumMod":
                lightness *= amount
            elif operation == "lumOff":
                lightness += amount
            elif operation == "satMod":
                saturation *= amount
            elif operation == "satOff":
                saturation += amount
            elif operation == "hueMod":
                hue *= amount
            elif operation == "hueOff":
                hue += int(transform.get("val", "0")) / 21600000
            rgb = colorsys.hls_to_rgb(hue % 1, _clamp(lightness), _clamp(saturation))
        rgb = tuple(_clamp(channel) for channel in rgb)
    return {"hex": _hex(rgb), "opacity": round(_clamp(alpha), 5), "token": kind, "value": raw}


def paint_colors(
    container: ET.Element | None,
    palette: dict[str, str],
    color_map: dict[str, str],
    placeholder: str | None = None,
) -> tuple[list[dict], str | None]:
    """Read a fill/line paint; unresolved media and exotic paint stay explicit."""
    if container is None:
        return [], "missing"
    kind = container.tag.rsplit("}", 1)[-1]
    if kind == "noFill":
        return [], None
    if kind in {"blipFill", "grpFill"}:
        return [], kind
    if kind not in {
        "solidFill",
        "gradFill",
        "pattFill",
        "outerShdw",
        "innerShdw",
        "glow",
        "highlight",
    } and not kind.startswith("ln"):
        return [], kind
    if kind == "ln" or kind.startswith("ln"):
        fill = next(
            (
                child
                for child in container
                if child.tag.rsplit("}", 1)[-1] in {"solidFill", "gradFill", "pattFill", "noFill"}
            ),
            None,
        )
        return paint_colors(fill, palette, color_map, placeholder)
    values = []
    parents = {child: parent for parent in container.iter() for child in parent}
    for token in container.iter():
        if token.tag.rsplit("}", 1)[-1] not in COLOR_TAGS:
            continue
        result = resolve_color(token, palette, color_map, placeholder)
        if result is None:
            return values, "unresolved-color"
        ancestor = parents.get(token)
        while ancestor is not None and ancestor is not container:
            tag = ancestor.tag.rsplit("}", 1)[-1]
            if tag == "gs":
                result["component"] = "gradient-stop"
                raw_position = ancestor.get("pos", "")
                if not raw_position.isdigit():
                    return values, "invalid-gradient-position"
                result["position"] = int(raw_position) / 100000
                break
            if tag in {"fgClr", "bgClr"}:
                result["component"] = (
                    "pattern-foreground" if tag == "fgClr" else "pattern-background"
                )
                break
            ancestor = parents.get(ancestor)
        values.append(result)
    return values, None if values else "missing-color"
