"""Resolve editable table cell paint from DrawingML table style precedence."""

from __future__ import annotations

import zipfile
from xml.etree import ElementTree as ET
from defusedxml.ElementTree import fromstring as safe_fromstring

A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def table_styles(package: zipfile.ZipFile) -> dict[str, ET.Element]:
    part = "ppt/tableStyles.xml"
    if part not in package.namelist():
        return {}
    root = safe_fromstring(package.read(part))
    return {node.get("styleId", ""): node for node in root.findall(f"{{{A}}}tblStyle")}


def cell_styles(
    table: ET.Element,
    row: int,
    column: int,
    rows: int,
    columns: int,
    styles: dict[str, ET.Element],
) -> list[tuple[str, ET.Element]]:
    """Return applicable styles from highest to lowest precedence."""
    properties = table.find(f"{{{A}}}tblPr")
    if properties is None:
        return []
    identifier = properties.findtext(f"{{{A}}}tableStyleId")
    style = styles.get(identifier or "")
    if style is None:
        return []
    names = []
    if properties.get("firstRow") == "1" and row == 0:
        if properties.get("firstCol") == "1" and column == 0:
            names.append("nwCell")
        if properties.get("lastCol") == "1" and column == columns - 1:
            names.append("neCell")
    if properties.get("lastRow") == "1" and row == rows - 1:
        if properties.get("firstCol") == "1" and column == 0:
            names.append("swCell")
        if properties.get("lastCol") == "1" and column == columns - 1:
            names.append("seCell")
    if row == 0 and properties.get("firstRow") == "1":
        names.append("firstRow")
    if row == rows - 1 and properties.get("lastRow") == "1":
        names.append("lastRow")
    if column == 0 and properties.get("firstCol") == "1":
        names.append("firstCol")
    if column == columns - 1 and properties.get("lastCol") == "1":
        names.append("lastCol")
    if properties.get("bandRow") == "1":
        names.append("band1H" if row % 2 == 0 else "band2H")
    if properties.get("bandCol") == "1":
        names.append("band1V" if column % 2 == 0 else "band2V")
    names.append("wholeTbl")
    return [(name, node) for name in names if (node := style.find(f"{{{A}}}{name}")) is not None]


def style_fill(styles: list[tuple[str, ET.Element]]) -> tuple[str, ET.Element] | None:
    for name, node in styles:
        wrapper = node.find(f"{{{A}}}tcStyle/{{{A}}}fill")
        if wrapper is not None and len(wrapper):
            return name, wrapper[0]
    return None


def style_text_color(styles: list[tuple[str, ET.Element]]) -> tuple[str, ET.Element] | None:
    for name, node in styles:
        text = node.find(f"{{{A}}}tcTxStyle")
        if text is None:
            continue
        for child in text:
            if child.tag.rsplit("}", 1)[-1] in {
                "schemeClr",
                "srgbClr",
                "sysClr",
                "prstClr",
                "scrgbClr",
            }:
                return name, child
    return None


def style_border(
    styles: list[tuple[str, ET.Element]],
    side: str,
    row: int,
    column: int,
    rows: int,
    columns: int,
) -> tuple[str, ET.Element] | None:
    """Find the first applicable cell border, including interior table rules."""
    interior = None
    if side in {"left", "right"} and (
        (side == "left" and column > 0) or (side == "right" and column < columns - 1)
    ):
        interior = "insideV"
    if side in {"top", "bottom"} and (
        (side == "top" and row > 0) or (side == "bottom" and row < rows - 1)
    ):
        interior = "insideH"
    for name, node in styles:
        border = node.find(f"{{{A}}}tcStyle/{{{A}}}tcBdr")
        if border is None:
            continue
        for key in (side, interior):
            if key is None:
                continue
            line = border.find(f"{{{A}}}{key}/{{{A}}}ln")
            if line is not None:
                return name, line
    return None
