"""Extract reusable table geometry and paint, never source cell content."""

from studio.composition.shape_geometry import box
from studio.models import TableCellStyle, TableTemplate

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _color(node):
    if node is None:
        return "", 0.0
    rgb = node.find(".//" + A + "srgbClr")
    if rgb is None or not rgb.get("val"):
        return "", 0.0
    alpha = rgb.find(A + "alpha")
    return "#" + rgb.get("val").upper(), int(
        alpha.get("val")
    ) / 100000 if alpha is not None else 1.0


def _cell_style(cell):
    props = cell._tc.get_or_add_tcPr()
    fill, opacity = _color(props.find(A + "solidFill"))
    paragraph = cell.text_frame.paragraphs[0]
    run = next(iter(paragraph.runs), None)
    font = run.font if run is not None else paragraph.font
    color = ""
    try:
        if font.color and font.color.type is not None and font.color.rgb is not None:
            color = "#" + str(font.color.rgb).upper()
    except (AttributeError, ValueError):
        pass
    borders = {}
    for side in "LRTB":
        node = props.find(A + "ln" + side)
        if node is not None and node.find(A + "noFill") is None:
            line_color, line_opacity = _color(node.find(A + "solidFill"))
            if line_color:
                borders[side] = {
                    "color": line_color,
                    "opacity": line_opacity,
                    "width": int(node.get("w", "6350")) / 12700,
                }
    return TableCellStyle(
        fill=fill,
        fill_opacity=opacity,
        color=color,
        font_size=font.size.pt if font.size else 0,
        bold=bool(font.bold),
        margins=[
            getattr(cell, "margin_" + side) / 12700 for side in ("left", "top", "right", "bottom")
        ],
        borders=borders,
    )


def table_template(shape):
    if not shape.has_table:
        return None
    table = shape.table
    if not len(table.rows) or not len(table.columns):
        return None
    if any(
        table.cell(ri, ci).is_merge_origin or table.cell(ri, ci).is_spanned
        for ri in range(len(table.rows))
        for ci in range(len(table.columns))
    ):
        return None  # A rectangular style grid cannot represent merged cells.
    return TableTemplate(
        box=box(shape),
        source_shape_id=shape.shape_id,
        column_widths=[col.width / 12700 for col in table.columns],
        row_heights=[row.height / 12700 for row in table.rows],
        cells=[
            [_cell_style(table.cell(ri, ci)) for ci in range(len(table.columns))]
            for ri in range(len(table.rows))
        ],
    )
