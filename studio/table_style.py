"""A single paint policy shared by native PPTX, HTML, PDF and chart fallback."""
from .template import contrast


def apply_table_style(element, pattern=None):
    if pattern is None or not pattern.table_style:
        # No source table style is not an instruction to erase all structure.
        # Keep the template accent, a light tint and visible row separators.
        element.fill_opacity=.22
        element.body_fill=element.fill
        element.body_fill_opacity=.06
        return element
    header = pattern.table_style.get("header", {})
    body = pattern.table_style.get("body", {})
    element.fill = header.get("color", "")
    element.fill_opacity = header.get("opacity", 0)
    element.body_fill = body.get("color", "")
    element.body_fill_opacity = body.get("opacity", 0)
    return element


def cell_paint(element, row, scene, profile):
    fill = element.fill if row == 0 else element.body_fill
    opacity = (element.fill_opacity if row == 0 else element.body_fill_opacity) if fill else 0
    background = element.background_hint or scene.background
    if opacity:
        rgb = [round(int(fill[i:i+2],16)*opacity + int(background[i:i+2],16)*(1-opacity)) for i in (1,3,5)]
        background = "#%02X%02X%02X" % tuple(rgb)
    color = max(profile.colors, key=lambda c: contrast(c, background))
    return fill, opacity, color


def cell_borders(cell, color):
    """Native editable row rules; no opaque plate over template artwork."""
    from pptx.oxml.xmlchemy import OxmlElement
    from pptx.oxml.ns import qn
    props=cell._tc.get_or_add_tcPr()
    for side in ('L','R','T','B'):
        for old in list(props.findall(qn('a:ln'+side))):
            props.remove(old)
        line=OxmlElement('a:ln'+side);line.set('w','6350')
        if side=='B':
            fill=OxmlElement('a:solidFill');rgb=OxmlElement('a:srgbClr')
            rgb.set('val',color.lstrip('#'));alpha=OxmlElement('a:alpha');alpha.set('val','30000')
            rgb.append(alpha);fill.append(rgb);line.append(fill)
        else:
            line.append(OxmlElement('a:noFill'))
        props.append(line)


def compact_table(element, profile):
    """Remove spare height after assigning each row its measured content height."""
    if element.kind != 'table' or not element.rows:
        return
    from .fonts import element_font
    path=element_font(profile,element)[1]
    widths=column_widths(element.rows,element.box.w,path,element.size)
    element.box.h=min(element.box.h,sum(row_heights(element.rows,widths,path,element.size)))


def row_heights(rows, widths, font_file, size, height=None):
    """Measure rows independently; constrained rows remain auditable on overflow.

    Header uses the same bold face and width allowance as table_cell_fits.
    Never expand a table beyond its reserved box or hide/truncate source cells.
    """
    from .fonts import wrap_text, _bold_measurement_face
    from pathlib import Path
    stat=Path(font_file).stat()
    bold=_bold_measurement_face(str(font_file),stat.st_mtime_ns,stat.st_size)
    natural=[max((len(wrap_text(str(cell),bold if ri==0 else font_file,size,
                              max(1,(widths[ci]-16)*(.94 if ri==0 else 1))))
                  for ci,cell in enumerate(row)),default=1)*size*1.25+16
             for ri,row in enumerate(rows)]
    if height is None or not natural:return natural
    total=sum(natural)
    if total>height:return [h*height/total for h in natural]
    spare=(height-total)/len(natural)
    return [h+spare for h in natural]


def column_widths(rows, width, font_file, size):
    """Share width according to measured cell text, keeping all columns/cells."""
    from .fonts import text_width
    count=max(map(len,rows),default=0)
    if not count:return []
    desired=[max(48,max(text_width(str(row[i]),font_file,size)*1.07+16
                       for row in rows if i<len(row))) for i in range(count)]
    from .fonts import _bold_measurement_face
    from pathlib import Path
    stat=Path(font_file).stat()
    bold=_bold_measurement_face(str(font_file),stat.st_mtime_ns,stat.st_size)
    floors=[max(32,max((text_width(word,bold if ri==0 else font_file,size)/(.94 if ri==0 else 1)+16
                        for ri,row in enumerate(rows) if i<len(row) for word in str(row[i]).split()),default=32))
            for i in range(count)]
    if sum(floors)>width:
        return [width*value/sum(floors) for value in floors]
    spare=width-sum(floors)
    weights=[max(1,value-floor) for value,floor in zip(desired,floors)]
    total=sum(weights)
    return [floor+spare*weight/total for floor,weight in zip(floors,weights)]
