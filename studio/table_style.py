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
    """Keep native equal rows readable without stretching them to the entire field."""
    if element.kind != 'table' or not element.rows:
        return
    from .fonts import element_font, wrap_text
    path=element_font(profile,element)[1]
    width=element.box.w/max(1,len(element.rows[0]))-16
    row_height=max(len(wrap_text(str(cell),path,element.size,max(1,width*(.94 if ri==0 else 1))))
                   for ri,row in enumerate(element.rows) for cell in row)*element.size*1.25+16
    element.box.h=min(element.box.h,row_height*len(element.rows))
