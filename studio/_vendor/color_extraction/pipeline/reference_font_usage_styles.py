"""OOXML font inheritance and script helpers for reference font inventory."""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from xml.etree import ElementTree as ET

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
SCRIPTS = ("latin", "ea", "cs", "sym")


def _placeholder(shape: ET.Element) -> tuple[str, str] | None:
    node = shape.find(f"{{{P}}}nvSpPr/{{{P}}}nvPr/{{{P}}}ph")
    if node is None:
        return None
    return node.get("type", "body"), node.get("idx", "0")


def _parent_shape(shape: ET.Element, root: ET.Element, *, master: bool) -> ET.Element | None:
    placeholder = _placeholder(shape)
    if placeholder is None:
        return None
    candidates = [node for node in root.findall(f".//{{{P}}}sp") if _placeholder(node)]
    if not master:
        match = next((node for node in candidates if _placeholder(node)[1] == placeholder[1]), None)
        if match is not None:
            return match
    kind = "title" if placeholder[0] == "ctrTitle" else placeholder[0]
    return next(
        (
            node
            for node in candidates
            if ("title" if _placeholder(node)[0] == "ctrTitle" else _placeholder(node)[0]) == kind
        ),
        None,
    )


def _role(shape: ET.Element | None, context: str) -> str:
    if context in {"table", "chart"}:
        return context
    if shape is None:
        return "other"
    placeholder = _placeholder(shape)
    if placeholder is None:
        return "other"
    return {
        "title": "title",
        "ctrTitle": "title",
        "subTitle": "subtitle",
        "body": "body",
        "ftr": "footer",
        "dt": "footer",
        "sldNum": "footer",
    }.get(placeholder[0], "other")


def _theme_fonts(theme: ET.Element | None) -> dict[tuple[str, str], str]:
    result = {}
    if theme is None:
        return result
    for role, tag in (("major", "majorFont"), ("minor", "minorFont")):
        group = theme.find(f".//{{{A}}}fontScheme/{{{A}}}{tag}")
        if group is None:
            continue
        for script in SCRIPTS[:3]:
            node = group.find(f"{{{A}}}{script}")
            if node is not None and node.get("typeface"):
                result[role, script] = node.get("typeface").strip()
    return result


def _resolve_name(name: str, script: str, theme: dict[tuple[str, str], str]) -> str:
    aliases = {"mj": "major", "mn": "minor", "lt": "latin", "ea": "ea", "cs": "cs"}
    if name.startswith("+") and "-" in name:
        group, token_script = name[1:].split("-", 1)
        return theme.get((aliases.get(group, group), aliases.get(token_script, script)), name)
    return name


def _script_counts(value: str) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for character in value:
        codepoint = ord(character)
        if 0xE000 <= codepoint <= 0xF8FF:
            script = "sym"
        elif (0x0590 <= codepoint <= 0x08FF) or (0xFB1D <= codepoint <= 0xFEFC):
            script = "cs"
        elif unicodedata.east_asian_width(character) in {"W", "F"}:
            script = "ea"
        else:
            script = "latin"
        result[script] += 1
    return result


def _source_properties(
    paragraph: ET.Element,
    run: ET.Element | None,
    shape_chain: list[tuple[str, ET.Element]],
    master: ET.Element | None,
    presentation: ET.Element,
    role: str,
    source: str,
) -> list[tuple[str, ET.Element]]:
    properties = []
    node = run.find(f"{{{A}}}rPr") if run is not None else paragraph.find(f"{{{A}}}endParaRPr")
    if node is not None:
        properties.append((source, node))
    ppr = paragraph.find(f"{{{A}}}pPr")
    level = min(9, max(1, int(ppr.get("lvl", "0")) + 1)) if ppr is not None else 1
    if ppr is not None:
        node = ppr.find(f"{{{A}}}defRPr")
        if node is not None:
            properties.append((source, node))
    for source, shape in shape_chain:
        body = shape.find(f"{{{P}}}txBody")
        if body is None:
            continue
        paths = [f"{{{A}}}lstStyle/{{{A}}}lvl{level}pPr/{{{A}}}defRPr"]
        if source != "slide":
            paths.insert(0, f"{{{A}}}p/{{{A}}}pPr/{{{A}}}defRPr")
        for path in paths:
            node = body.find(path)
            if node is not None:
                properties.append((source, node))
    if master is not None:
        style = (
            "titleStyle"
            if role == "title"
            else "bodyStyle"
            if shape_chain and _placeholder(shape_chain[0][1])
            else "otherStyle"
        )
        node = master.find(f"{{{P}}}txStyles/{{{P}}}{style}/{{{A}}}lvl{level}pPr/{{{A}}}defRPr")
        if node is not None:
            properties.append(("master", node))
    node = presentation.find(f"{{{P}}}defaultTextStyle/{{{A}}}lvl{level}pPr/{{{A}}}defRPr")
    if node is not None:
        properties.append(("presentation", node))
    return properties


def _effective_font(
    properties: list[tuple[str, ET.Element]],
    script: str,
    role: str,
    theme: dict[tuple[str, str], str],
) -> tuple[str, str, int | None, int | None, bool | None] | None:
    for candidate in (script, "latin") if script != "latin" else (script,):
        for origin, node in properties:
            face = node.find(f"{{{A}}}{candidate}")
            if face is not None and face.get("typeface"):
                family = _resolve_name(face.get("typeface").strip(), candidate, theme)
                source = origin
                break
        else:
            continue
        break
    else:
        family = theme.get(("major" if role == "title" else "minor", script)) or theme.get(
            ("major" if role == "title" else "minor", "latin")
        )
        source = "theme"
    if not family:
        return None
    weight = next(
        (
            700 if node.get("b") == "1" else 400
            for _, node in properties
            if node.get("b") in {"0", "1"}
        ),
        None,
    )
    italic = next(
        (node.get("i") == "1" for _, node in properties if node.get("i") in {"0", "1"}),
        None,
    )
    size = next(
        (int(node.get("sz")) for _, node in properties if node.get("sz", "").isdigit()),
        None,
    )
    return family, source, size, weight, italic
