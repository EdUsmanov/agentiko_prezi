"""A single paint policy shared by native PPTX, HTML, PDF and chart fallback."""

from studio.templates.template_geometry import contrast


def apply_table_style(element, pattern=None):
    template = getattr(pattern, "table_template", None)
    if (
        template
        and element.rows
        and all(len(row) == len(template.column_widths) for row in element.rows)
    ):
        element.table_template = template.model_copy(deep=True)
        element.box = template.box.model_copy(deep=True)
        for ri, cells in enumerate(element.table_template.cells):
            paint = (pattern.table_style or {}).get("header" if ri == 0 else "body", {})
            for cell in cells:
                if not cell.fill and paint.get("color"):
                    cell.fill = paint["color"]
                    cell.fill_opacity = paint.get("opacity", 1)
        return element
    if pattern is None or not pattern.table_style:
        # No source table style is not an instruction to erase all structure.
        # Keep the template accent, a light tint and visible row separators.
        element.fill_opacity = 0.22
        element.body_fill = element.fill
        element.body_fill_opacity = 0.06
        return element
    header = pattern.table_style.get("header", {})
    body = pattern.table_style.get("body", {})
    element.fill = header.get("color", "")
    element.fill_opacity = header.get("opacity", 0)
    element.body_fill = body.get("color", "")
    element.body_fill_opacity = body.get("opacity", 0)
    return element


def cell_paint(element, row, scene, profile):
    template = getattr(element, "table_template", None)
    if template:
        style = table_cell_style(element, row, 0)
        if style:
            return (
                style.fill,
                style.fill_opacity,
                style.color or element.color or profile.foreground,
            )
    fill = element.fill if row == 0 else element.body_fill
    opacity = (element.fill_opacity if row == 0 else element.body_fill_opacity) if fill else 0
    background = element.background_hint or scene.background
    if opacity:
        rgb = [
            round(
                int(fill[i : i + 2], 16) * opacity + int(background[i : i + 2], 16) * (1 - opacity)
            )
            for i in (1, 3, 5)
        ]
        background = "#%02X%02X%02X" % tuple(rgb)
    color = max(profile.colors, key=lambda c: contrast(c, background))
    return fill, opacity, color


def table_cell_style(element, row, col):
    template = getattr(element, "table_template", None)
    if not template or not template.cells:
        return None
    # Preserve authored header and body rows; repeat body styling for new rows.
    source_row = (
        row
        if row < len(template.cells)
        else (1 + (row - 1) % (len(template.cells) - 1) if len(template.cells) > 1 else 0)
    )
    cells = template.cells[source_row]
    return cells[col] if col < len(cells) else None


def table_widths(element, font_file):
    template = getattr(element, "table_template", None)
    if template and element.rows and len(template.column_widths) == len(element.rows[0]):
        total = sum(template.column_widths)
        if total > 0:
            return [element.box.w * w / total for w in template.column_widths]
    return column_widths(element.rows, element.box.w, font_file, element.size)


def table_heights(element, widths, font_file):
    template = getattr(element, "table_template", None)
    if template and template.row_heights:
        heights = [
            template.row_heights[min(i, len(template.row_heights) - 1)]
            for i in range(len(element.rows))
        ]
        total = sum(heights)
        if total > 0:
            return [element.box.h * h / total for h in heights]
    return row_heights(element.rows, widths, font_file, element.size, element.box.h)


def table_geometry(element, profile):
    from studio.templates.fonts import element_font

    font_file = element_font(profile, element)[1]
    widths = table_widths(element, font_file)
    return widths, table_heights(element, widths, font_file)


def cell_borders(cell, color, style=None):
    """Native editable row rules; no opaque plate over template artwork."""
    from pptx.oxml.xmlchemy import OxmlElement
    from pptx.oxml.ns import qn

    props = cell._tc.get_or_add_tcPr()
    for side in ("L", "R", "T", "B"):
        for old in list(props.findall(qn("a:ln" + side))):
            props.remove(old)
        line = OxmlElement("a:ln" + side)
        border = style.borders.get(side) if style else None
        line.set("w", str(round(border.width * 12700)) if border else "6350")
        if border or (style is None and side == "B"):
            fill = OxmlElement("a:solidFill")
            rgb = OxmlElement("a:srgbClr")
            rgb.set("val", (border.color if border else color).lstrip("#"))
            alpha = OxmlElement("a:alpha")
            alpha.set("val", str(round((border.opacity if border else 0.3) * 100000)))
            rgb.append(alpha)
            fill.append(rgb)
            line.append(fill)
        else:
            line.append(OxmlElement("a:noFill"))
        props.append(line)


def compact_table(element, profile):
    """Remove spare height after assigning each row its measured content height."""
    if element.kind != "table" or not element.rows:
        return
    from studio.templates.fonts import element_font

    if getattr(element, "table_template", None):
        return  # The authored table box and its row geometry are intentional.

    path = element_font(profile, element)[1]
    widths = column_widths(element.rows, element.box.w, path, element.size)
    element.box.h = min(element.box.h, sum(row_heights(element.rows, widths, path, element.size)))


def row_heights(rows, widths, font_file, size, height=None):
    """Measure rows independently; constrained rows remain auditable on overflow.

    Header uses the same bold face and width allowance as table_cell_fits.
    Never expand a table beyond its reserved box or hide/truncate source cells.
    """
    from studio.templates.fonts import wrap_text, _bold_measurement_face
    from pathlib import Path

    stat = Path(font_file).stat()
    bold = _bold_measurement_face(str(font_file), stat.st_mtime_ns, stat.st_size)
    natural = [
        max(
            (
                len(
                    wrap_text(
                        str(cell),
                        bold if ri == 0 else font_file,
                        size,
                        max(1, (widths[ci] - 16) * (0.94 if ri == 0 else 1)),
                    )
                )
                for ci, cell in enumerate(row)
            ),
            default=1,
        )
        * size
        * 1.25
        + 16
        for ri, row in enumerate(rows)
    ]
    if height is None or not natural:
        return natural
    total = sum(natural)
    if total > height:
        return [h * height / total for h in natural]
    spare = (height - total) / len(natural)
    return [h + spare for h in natural]


def column_widths(rows, width, font_file, size):
    """Share width according to measured cell text, keeping all columns/cells."""
    from studio.templates.fonts import text_width

    count = max(map(len, rows), default=0)
    if not count:
        return []
    desired = [
        max(
            48,
            max(
                text_width(str(row[i]), font_file, size) * 1.07 + 16 for row in rows if i < len(row)
            ),
        )
        for i in range(count)
    ]
    from studio.templates.fonts import _bold_measurement_face
    from pathlib import Path

    stat = Path(font_file).stat()
    bold = _bold_measurement_face(str(font_file), stat.st_mtime_ns, stat.st_size)
    floors = [
        max(
            32,
            max(
                (
                    text_width(word, bold if ri == 0 else font_file, size)
                    / (0.94 if ri == 0 else 1)
                    + 16
                    for ri, row in enumerate(rows)
                    if i < len(row)
                    for word in str(row[i]).split()
                ),
                default=32,
            ),
        )
        for i in range(count)
    ]
    if sum(floors) > width:
        return [width * value / sum(floors) for value in floors]
    spare = width - sum(floors)
    weights = [max(1, value - floor) for value, floor in zip(desired, floors)]
    total = sum(weights)
    return [floor + spare * weight / total for floor, weight in zip(floors, weights)]
