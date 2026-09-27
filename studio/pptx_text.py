"""OOXML text and color writing shared by slide and chart renderers."""

from pptx.util import Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR
from pptx.oxml.xmlchemy import OxmlElement
from .fonts import element_font, wrap_text, font_runs
from .font_identity import apply_ooxml_font, ooxml_face


def rgb(value):
    return RGBColor.from_string(value.lstrip("#"))


def set_text(frame, text, e, profile, width=None):
    frame.clear()
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    frame.word_wrap = False
    frame.vertical_anchor = MSO_ANCHOR.TOP
    inset = e.size * 1.4 if e.bullet else 0
    family, font_file = element_font(profile, e)
    lines = wrap_text(
        text,
        font_file,
        e.size,
        ((width or e.box.w) - inset) * (0.94 if e.bold or e.bold_prefix else 1),
    )
    p = frame.paragraphs[0]
    p.space_before = Pt(0)
    p.space_after = Pt(0)
    p.line_spacing = Pt(e.size * 1.25)
    if e.bullet:
        props = p._p.get_or_add_pPr()
        props.set("marL", str(int(Pt(inset))))
        props.set("indent", str(-int(Pt(e.size))))
        bullet_color = OxmlElement("a:buClr")
        color = OxmlElement("a:srgbClr")
        color.set("val", e.color.lstrip("#"))
        bullet_color.append(color)
        props.append(bullet_color)
        bullet_size = OxmlElement("a:buSzPct")
        bullet_size.set("val", "100000")
        props.append(bullet_size)
        bullet_face = next(font_runs("•", font_file))[1]
        bullet_font = OxmlElement("a:buFont")
        bullet_font.set("typeface", ooxml_face(bullet_face)[0])
        props.append(bullet_font)
        bullet = OxmlElement("a:buChar")
        bullet.set("char", "•")
        props.append(bullet)
    # Bullet glyphs use paragraph defaults, not necessarily the first run.
    apply_ooxml_font(p.font, font_file, e.bold)
    p.font.size = Pt(e.size)
    p.font.color.rgb = rgb(e.color)
    for i, line in enumerate(lines):
        if i:
            p._p.append(OxmlElement("a:br"))
        segments = [(line, e.bold)]
        if i == 0 and e.bold_prefix and line.startswith(e.bold_prefix):
            segments = [(e.bold_prefix, True), (line[len(e.bold_prefix) :], e.bold)]
        for value, bold in segments:
            for fragment, face in font_runs(value, font_file):
                run = p.add_run()
                run.text = fragment
                apply_ooxml_font(run.font, face, bold)
                run.font.size = Pt(e.size)
                run.font.color.rgb = rgb(e.color)
