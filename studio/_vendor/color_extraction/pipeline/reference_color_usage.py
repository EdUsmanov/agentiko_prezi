"""Per-slide effective editable colors in PPTX/POTX, with OOXML evidence."""

from __future__ import annotations

import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET
from defusedxml.ElementTree import fromstring as safe_fromstring

from studio._vendor.color_extraction.domain.archive_safety import validate_archive
from studio._vendor.color_extraction.pipeline.ooxml_resolution import effective_theme, inheritance_parts
from studio._vendor.color_extraction.pipeline.reference_color_bullets import bullet_color
from studio._vendor.color_extraction.pipeline.reference_color_elements import A, P, _background, _first_paint, _id, _record, _style_paint, _text_color
from studio._vendor.color_extraction.pipeline.reference_color_package import ordered_slides
from studio._vendor.color_extraction.pipeline.reference_color_tables import cell_styles, style_border, style_fill, style_text_color, table_styles
from studio._vendor.color_extraction.pipeline.reference_color_values import paint_colors, resolve_color, theme_colors
from studio._vendor.color_extraction.pipeline.reference_font_usage_styles import _parent_shape, _placeholder, _role

TEXT_RUNS = {f"{{{A}}}r", f"{{{A}}}fld"}


def _shape_paint(
    shape: ET.Element,
    ancestors: list[tuple[str, ET.Element]],
    source: str,
    theme: ET.Element | None,
    palette: dict[str, str],
    color_map: dict[str, str],
    uses: list[dict],
    unresolved: list[dict],
    parents: dict[ET.Element, ET.Element],
) -> None:
    shape_id = _id(shape, source)
    for role in ("fill", "line"):
        for origin, candidate in ancestors:
            properties = candidate.find(f"{{{P}}}spPr")
            paint = (
                _first_paint(properties)
                if role == "fill"
                else properties.find(f"{{{A}}}ln")
                if properties is not None
                else None
            )
            if paint is not None:
                if paint.tag == f"{{{A}}}grpFill":
                    group = parents.get(shape)
                    while group is not None:
                        if group.tag == f"{{{P}}}grpSp":
                            inherited = _first_paint(group.find(f"{{{P}}}grpSpPr"))
                            if inherited is not None and inherited.tag != f"{{{A}}}grpFill":
                                paint = inherited
                                break
                        group = parents.get(group)
                colors, reason = paint_colors(paint, palette, color_map)
                if reason == "missing" and role == "line":
                    styled = _style_paint(candidate, role, theme, palette, color_map)
                    if styled is not None:
                        _record(
                            uses, unresolved, shape_id, f"shape.{role}", f"{origin}-style", *styled
                        )
                        break
                    continue
                _record(uses, unresolved, shape_id, f"shape.{role}", origin, colors, reason)
                break
            styled = _style_paint(candidate, role, theme, palette, color_map)
            if styled is not None:
                _record(uses, unresolved, shape_id, f"shape.{role}", f"{origin}-style", *styled)
                break
    for origin, candidate in ancestors:
        effects = candidate.find(f"{{{P}}}spPr/{{{A}}}effectLst")
        if effects is None:
            continue
        for effect in effects:
            if effect.tag.rsplit("}", 1)[-1] in {"outerShdw", "innerShdw", "glow"}:
                _record(
                    uses,
                    unresolved,
                    shape_id,
                    "shape.effect",
                    origin,
                    *paint_colors(effect, palette, color_map),
                )
        break


def _shape_text(
    shape: ET.Element,
    ancestors: list[tuple[str, ET.Element]],
    source: str,
    master: ET.Element | None,
    presentation: ET.Element,
    theme: ET.Element | None,
    palette: dict[str, str],
    color_map: dict[str, str],
    uses: list[dict],
    unresolved: list[dict],
) -> None:
    body = shape.find(f"{{{P}}}txBody")
    if body is None:
        return
    role = _role(shape, "shape")
    shape_id = _id(shape, source)
    for paragraph in body.findall(f"{{{A}}}p"):
        visible_runs = [
            run
            for run in paragraph
            if run.tag in TEXT_RUNS and (run.findtext(f"{{{A}}}t") or "").strip()
        ]
        if visible_runs:
            bullet = bullet_color(
                paragraph, ancestors, master, presentation, role, source, palette, color_map
            )
            if bullet is not None:
                _record(uses, unresolved, shape_id, "text.bullet", bullet[1], bullet[0], bullet[2])
        for run in paragraph:
            if run.tag not in TEXT_RUNS or not (run.findtext(f"{{{A}}}t") or "").strip():
                continue
            colors, origin, reason = _text_color(
                paragraph,
                run,
                ancestors,
                master,
                presentation,
                role,
                source,
                theme,
                palette,
                color_map,
            )
            _record(uses, unresolved, shape_id, f"text.{role}", origin, colors, reason)
            properties = run.find(f"{{{A}}}rPr")
            if properties is not None:
                highlight = properties.find(f"{{{A}}}highlight")
                if highlight is not None:
                    _record(
                        uses,
                        unresolved,
                        shape_id,
                        "text.highlight",
                        source,
                        *paint_colors(highlight, palette, color_map),
                    )
                effects = properties.find(f"{{{A}}}effectLst")
                if effects is not None:
                    for effect in effects:
                        if effect.tag.rsplit("}", 1)[-1] in {"outerShdw", "innerShdw", "glow"}:
                            _record(
                                uses,
                                unresolved,
                                shape_id,
                                "text.effect",
                                source,
                                *paint_colors(effect, palette, color_map),
                            )


def _table(
    frame: ET.Element,
    source: str,
    master: ET.Element | None,
    presentation: ET.Element,
    theme: ET.Element | None,
    palette: dict[str, str],
    color_map: dict[str, str],
    uses: list[dict],
    unresolved: list[dict],
    styles: dict[str, ET.Element],
) -> None:
    shape_id = _id(frame, source)
    table = frame.find(f".//{{{A}}}tbl")
    if table is None:
        return
    rows = table.findall(f"{{{A}}}tr")
    columns = max((len(row.findall(f"{{{A}}}tc")) for row in rows), default=0)
    for row_index, row in enumerate(rows):
        for column_index, cell in enumerate(row.findall(f"{{{A}}}tc")):
            cell_id = f"{shape_id}:r{row_index}c{column_index}"
            matched_styles = cell_styles(table, row_index, column_index, len(rows), columns, styles)
            properties = cell.find(f"{{{A}}}tcPr")
            paint = _first_paint(properties)
            if paint is not None:
                _record(
                    uses,
                    unresolved,
                    cell_id,
                    "table.fill",
                    source,
                    *paint_colors(paint, palette, color_map),
                )
            elif styled := style_fill(matched_styles):
                name, paint = styled
                _record(
                    uses,
                    unresolved,
                    cell_id,
                    "table.fill",
                    f"table-style:{name}",
                    *paint_colors(paint, palette, color_map),
                )
            elif not matched_styles:
                unresolved.append(
                    {
                        "shapeId": cell_id,
                        "role": "table.fill",
                        "source": source,
                        "reason": "table-style-or-default",
                    }
                )
            if properties is not None:
                for border in properties:
                    if border.tag.rsplit("}", 1)[-1].startswith("ln"):
                        _record(
                            uses,
                            unresolved,
                            cell_id,
                            "table.line",
                            source,
                            *paint_colors(border, palette, color_map),
                        )
            for side, tag in (("left", "lnL"), ("right", "lnR"), ("top", "lnT"), ("bottom", "lnB")):
                if properties is not None and properties.find(f"{{{A}}}{tag}") is not None:
                    continue
                styled_border = style_border(
                    matched_styles, side, row_index, column_index, len(rows), columns
                )
                if styled_border is not None:
                    name, line = styled_border
                    _record(
                        uses,
                        unresolved,
                        cell_id,
                        "table.line",
                        f"table-style:{name}:{side}",
                        *paint_colors(line, palette, color_map),
                    )
            for paragraph in cell.findall(f"{{{A}}}txBody/{{{A}}}p"):
                for run in paragraph:
                    if run.tag not in TEXT_RUNS or not (run.findtext(f"{{{A}}}t") or "").strip():
                        continue
                    colors, origin, reason = _text_color(
                        paragraph,
                        run,
                        [],
                        master,
                        presentation,
                        "table",
                        source,
                        theme,
                        palette,
                        color_map,
                    )
                    if origin in {"master", "presentation", "theme-default"} and (
                        styled_text := style_text_color(matched_styles)
                    ):
                        name, token = styled_text
                        resolved = resolve_color(token, palette, color_map)
                        colors = [resolved] if resolved else []
                        origin = f"table-style:{name}"
                        reason = None if resolved else "unresolved-table-text-style"
                    _record(uses, unresolved, cell_id, "text.table", origin, colors, reason)


def extract_reference_color_usage(data: bytes, presentation_name: str) -> dict:
    """Inventory concrete slide colors; disclose any paint that cannot be resolved."""
    with zipfile.ZipFile(BytesIO(data)) as package:
        validate_archive(package)
        names = set(package.namelist())
        if "ppt/presentation.xml" not in names:
            raise ValueError("Invalid PPTX/POTX reference")
        presentation = safe_fromstring(package.read("ppt/presentation.xml"))
        styles = table_styles(package)
        slides = []
        for number, part in enumerate(ordered_slides(package, names, presentation), 1):
            chain = inheritance_parts(package, part)
            roots = [safe_fromstring(package.read(name)) for name in chain]
            theme, color_map = effective_theme(package, part)
            palette = theme_colors(theme)
            master = roots[-1] if len(roots) == 3 else None
            uses: list[dict] = []
            unresolved: list[dict] = []
            excluded: list[dict] = []
            _background(roots, theme, palette, color_map, uses, unresolved)
            for index, root in enumerate(roots):
                source = ("slide", "layout", "master")[index]
                if index and roots[0].get("showMasterSp") == "0":
                    continue
                if index == 2 and len(roots) > 1 and roots[1].get("showMasterSp") == "0":
                    continue
                tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
                if tree is None:
                    continue
                parents = {child: parent for parent in tree.iter() for child in parent}
                for shape in tree.iter():
                    if shape.tag not in {
                        f"{{{P}}}sp",
                        f"{{{P}}}cxnSp",
                        f"{{{P}}}graphicFrame",
                        f"{{{P}}}pic",
                    }:
                        continue
                    if index and _placeholder(shape) is not None:
                        continue
                    if shape.find(f".//{{{A}}}prstTxWarp") is not None:
                        excluded.append({"shapeId": _id(shape, source), "reason": "wordart"})
                        continue
                    if shape.tag == f"{{{P}}}pic":
                        excluded.append({"shapeId": _id(shape, source), "reason": "picture"})
                        continue
                    if shape.tag == f"{{{P}}}graphicFrame":
                        if shape.find(f".//{{{A}}}tbl") is not None:
                            _table(
                                shape,
                                source,
                                master,
                                presentation,
                                theme,
                                palette,
                                color_map,
                                uses,
                                unresolved,
                                styles,
                            )
                        else:
                            excluded.append(
                                {"shapeId": _id(shape, source), "reason": "dynamic-graphic"}
                            )
                        continue
                    ancestors = [(source, shape)]
                    if index == 0:
                        previous = shape
                        for parent_index, parent in enumerate(roots[1:], 1):
                            matched = _parent_shape(previous, parent, master=parent_index > 1)
                            if matched is not None:
                                ancestors.append((("layout", "master")[parent_index - 1], matched))
                                previous = matched
                    _shape_paint(
                        shape,
                        ancestors,
                        source,
                        theme,
                        palette,
                        color_map,
                        uses,
                        unresolved,
                        parents,
                    )
                    _shape_text(
                        shape,
                        ancestors,
                        source,
                        master,
                        presentation,
                        theme,
                        palette,
                        color_map,
                        uses,
                        unresolved,
                    )
            colors_by_role: dict[str, list[str]] = {}
            for use in uses:
                values = colors_by_role.setdefault(use["role"], [])
                if use["color"] not in values:
                    values.append(use["color"])
            excluded.extend(
                {"shapeId": item["shapeId"], "reason": "image-fill"}
                for item in unresolved
                if item["reason"] == "blipFill"
            )
            unresolved = [item for item in unresolved if item["reason"] != "blipFill"]
            slides.append(
                {
                    "number": number,
                    "part": part,
                    "colorsByRole": {
                        key: sorted(value) for key, value in sorted(colors_by_role.items())
                    },
                    "uses": uses,
                    "unresolved": unresolved,
                    "excluded": excluded,
                }
            )
        return {"presentation": presentation_name, "slideCount": len(slides), "slides": slides}
