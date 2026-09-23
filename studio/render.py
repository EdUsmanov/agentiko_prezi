"""All exports consume the same scene; PPTX remains native and editable."""
import base64
from copy import deepcopy
from html import escape
from pathlib import Path
import json
import re
import threading
from zipfile import ZipFile
from pptx import Presentation
from pptx.util import Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR
from pptx.oxml.xmlchemy import OxmlElement
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.colors import HexColor
from .models import Element, Box
from .fonts import pdf_font, wrap_text
from .template import contrast
from .embedded_fonts import check_glyphs, P, R

_pdfium_lock=threading.Lock()

def primitives(element,profile):
    if element.kind!="chart":
        return [element]
    e=element; b=e.box
    label_w=b.w*.3
    row_h=(b.h-e.size*2)/len(e.values)
    values_w=b.w-label_w-65
    scale=max(e.values)
    out=[]
    for i,(label,value) in enumerate(zip(e.labels,e.values)):
        y=b.y+i*row_h
        out.append(Element(kind="text",box=Box(x=b.x,y=y,w=label_w-10,h=row_h-6),text=label,font=e.font,size=e.size,color=e.color))
        bar_w=max(.5,values_w*value/scale)
        out.append(Element(kind="rect",box=Box(x=b.x+label_w,y=y+3,w=bar_w,h=max(8,min(row_h-12,25))),fill=e.fill))
        out.append(Element(kind="text",box=Box(x=b.x+label_w+bar_w+7,y=y,w=58,h=row_h-6),text=f"{value:g}",font=e.font,size=e.size,color=e.color))
    out.append(Element(kind="text",box=Box(x=b.x,y=b.y+b.h-e.size*1.6,w=b.w,h=e.size*1.6),text=e.unit,font=e.font,size=e.size,color=e.color,role="label"))
    return out

def rgb(value):
    return RGBColor.from_string(value.lstrip("#"))

def set_text(frame,text,e,profile,width=None):
    frame.clear()
    frame.margin_left=frame.margin_right=frame.margin_top=frame.margin_bottom=0
    frame.word_wrap=False
    frame.vertical_anchor=MSO_ANCHOR.TOP
    lines=wrap_text(text,profile.font_file,e.size,width or e.box.w)
    for i,line in enumerate(lines):
        p=frame.paragraphs[0] if i==0 else frame.add_paragraph()
        p.text=line
        p.space_before=Pt(0);p.space_after=Pt(0);p.line_spacing=Pt(e.size*1.25)
        for run in p.runs:
            run.font.name=e.font
            run.font.size=Pt(e.size)
            run.font.bold=e.bold
            run.font.color.rgb=rgb(e.color)

def clean_base(source,profile):
    prs=Presentation(source)
    # Keep only the validated font part actually selected for this package.
    selected=profile.font_origin.get("relationship_id") if profile.font_origin.get("kind")=="embedded" else None
    font_list=prs.part._element.find(P+"embeddedFontLst")
    if font_list is not None:
        for item in list(font_list):
            for node in list(item):
                if node.tag!=P+"font" and node.get(R+"id")!=selected:
                    item.remove(node)
            if not any(node.get(R+"id")==selected for node in item if node.tag!=P+"font"):
                font_list.remove(item)
        if not len(font_list):
            prs.part._element.remove(font_list)
    for rel in list(prs.part.rels.values()):
        if rel.reltype.endswith("/font") and rel.rId!=selected:
            prs.part.drop_rel(rel.rId)
    for entry in list(prs.slides._sldIdLst):
        prs.part.drop_rel(entry.rId)
        prs.slides._sldIdLst.remove(entry)
    # Keep actual source master/layout/theme relationships, but never old slide copy.
    for surface in list(prs.slide_masters)+[l for m in prs.slide_masters for l in m.slide_layouts]:
        for shape in list(surface.shapes):
            shape._element.getparent().remove(shape._element)
        for rel in list(surface.part.rels.values()):
            if rel.is_external or rel.reltype.rsplit("/",1)[-1] not in ("theme","slideLayout","slideMaster"):
                surface.part.drop_rel(rel.rId)
    for rel in list(prs.part.rels.values()):
        if rel.is_external:
            prs.part.drop_rel(rel.rId)
    props=prs.core_properties
    props.author="Presentation Studio";props.last_modified_by="Presentation Studio"
    props.title="Generated presentation";props.subject="";props.comments="";props.keywords=""
    return prs

def render_pptx(scenes,profile,source,path):
    prs=clean_base(source,profile)
    for scene in scenes:
        slide=prs.slides.add_slide(prs.slide_layouts[profile.layout_index])
        for shape in list(slide.shapes):
            shape._element.getparent().remove(shape._element)
        slide._element.set("showMasterSp","0")
        slide.background.fill.solid();slide.background.fill.fore_color.rgb=rgb(scene.background)
        for original in scene.elements:
            for e in primitives(original,profile):
                b=e.box
                if e.kind=="text":
                    shape=slide.shapes.add_textbox(Pt(b.x),Pt(b.y),Pt(b.w),Pt(b.h))
                    set_text(shape.text_frame,e.text,e,profile)
                elif e.kind in ("rect","line"):
                    shape=slide.shapes.add_shape(MSO_SHAPE.RECTANGLE,Pt(b.x),Pt(b.y),Pt(b.w),Pt(max(b.h,.5)))
                    shape.fill.solid();shape.fill.fore_color.rgb=rgb(e.fill or e.color)
                    shape.line.fill.background()
                elif e.kind=="image":
                    slide.shapes.add_picture(e.image_path,Pt(b.x),Pt(b.y),Pt(b.w),Pt(b.h))
                elif e.kind=="table":
                    rows,cols=len(e.rows),len(e.rows[0])
                    table=slide.shapes.add_table(rows,cols,Pt(b.x),Pt(b.y),Pt(b.w),Pt(b.h)).table
                    table.first_row=False;table.horz_banding=False
                    for ri,row in enumerate(e.rows):
                        table.rows[ri].height=Pt(b.h/rows)
                        for ci,value in enumerate(row):
                            cell=table.cell(ri,ci)
                            fill=e.fill if ri==0 else scene.background
                            color=max(profile.colors,key=lambda c:contrast(c,fill)) if ri==0 else e.color
                            cell.fill.solid();cell.fill.fore_color.rgb=rgb(fill)
                            cell.margin_left=cell.margin_right=Pt(8);cell.margin_top=cell.margin_bottom=Pt(6)
                            te=e.model_copy(update={"color":color,"bold":ri==0})
                            set_text(cell.text_frame,value,te,profile,b.w/cols-16)
                            cell.text_frame.margin_left=cell.text_frame.margin_right=Pt(8)
                            cell.text_frame.margin_top=cell.text_frame.margin_bottom=Pt(6)
                            tcPr=cell._tc.get_or_add_tcPr()
                            for side in ("L","R","T","B"):
                                ln=OxmlElement("a:ln"+side);ln.append(OxmlElement("a:noFill"));tcPr.append(ln)
        slide.notes_slide.notes_text_frame.text=scene.notes
    prs.save(path)
    # Reopen native output and check its object content rather than trusting save().
    check=Presentation(path)
    if len(check.slides)!=len(scenes) or any(not any(sh.has_text_frame for sh in s.shapes) for s in check.slides):
        raise ValueError("PPTX не прошёл проверку редактируемости")
    with ZipFile(path) as z:
        if any(b'TargetMode="External"' in z.read(n) for n in z.namelist() if n.endswith(".rels")):
            raise ValueError("В выходном PPTX осталась внешняя ссылка")

def render_pdf(scenes,profile,path):
    name=pdf_font(profile.font_file)
    c=Canvas(str(path),pagesize=(profile.width,profile.height),pageCompression=1)
    c.setTitle("Presentation Studio");c.setAuthor("Presentation Studio")
    def draw_text(text,x,y,w,size,color):
        c.setFillColor(HexColor(color));c.setFont(name,size)
        for li,line in enumerate(wrap_text(text,profile.font_file,size,w)):
            c.drawString(x,profile.height-y-size-li*size*1.25,line)
    for scene in scenes:
        c.setFillColor(HexColor(scene.background));c.rect(0,0,profile.width,profile.height,stroke=0,fill=1)
        for original in scene.elements:
            for e in primitives(original,profile):
                b=e.box
                if e.kind=="text":
                    draw_text(e.text,b.x,b.y,b.w,e.size,e.color)
                elif e.kind in ("rect","line"):
                    c.setFillColor(HexColor(e.fill or e.color));c.rect(b.x,profile.height-b.y-b.h,b.w,b.h,stroke=0,fill=1)
                elif e.kind=="image":
                    c.drawImage(e.image_path,b.x,profile.height-b.y-b.h,b.w,b.h,mask="auto")
                elif e.kind=="table":
                    rw=b.h/len(e.rows);cw=b.w/len(e.rows[0])
                    for ri,row in enumerate(e.rows):
                        for ci,value in enumerate(row):
                            fill=e.fill if ri==0 else scene.background
                            color=max(profile.colors,key=lambda v:contrast(v,fill)) if ri==0 else e.color
                            c.setFillColor(HexColor(fill));c.rect(b.x+ci*cw,profile.height-b.y-(ri+1)*rw,cw,rw,stroke=0,fill=1)
                            draw_text(value,b.x+ci*cw+8,b.y+ri*rw+6,cw-16,e.size,color)
        c.showPage()
    c.save()

def render_html(scenes,profile,path):
    # No remote resources, scripts or user CSS. Every user string is escaped.
    font_data=base64.b64encode(Path(profile.font_file).read_bytes()).decode()
    css=f"""@font-face{{font-family:DeckFont;src:url(data:font/ttf;base64,{font_data})}}*{{box-sizing:border-box}}body{{margin:0;background:#e8e8ec;display:grid;gap:24px;padding:24px;justify-content:center}}.slide{{position:relative;width:{profile.width}px;height:{profile.height}px;overflow:hidden;box-shadow:0 4px 18px #0002}}.el{{position:absolute;font-family:DeckFont;white-space:pre;line-height:1.25}}table{{border-collapse:collapse;table-layout:fixed;width:100%;height:100%;font-weight:normal}}td{{padding:6px 8px;vertical-align:top;white-space:pre-wrap;line-height:1.25}}@media print{{body{{padding:0;gap:0}}.slide{{break-after:page;box-shadow:none}}}}"""
    out=['<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Презентация</title><style>'+css+'</style><body>']
    for scene in scenes:
        out.append(f'<section class="slide" aria-label="{escape(scene.title,quote=True)}" style="background:{scene.background}">')
        for original in scene.elements:
            for e in primitives(original,profile):
                b=e.box
                style=f"left:{b.x}px;top:{b.y}px;width:{b.w}px;height:{b.h}px;font-size:{e.size}px;color:{e.color or profile.foreground};font-weight:{700 if e.bold else 400};"
                if e.kind=="text":
                    content="\n".join(wrap_text(e.text,profile.font_file,e.size,b.w))
                    out.append(f'<div class="el" style="{style}">{escape(content)}</div>')
                elif e.kind in ("rect","line"):
                    out.append(f'<div class="el" aria-hidden="true" style="{style}background:{e.fill or e.color}"></div>')
                elif e.kind=="image":
                    data=base64.b64encode(Path(e.image_path).read_bytes()).decode()
                    out.append(f'<img class="el" alt="Элемент шаблона" style="{style}" src="data:image/png;base64,{data}">')
                elif e.kind=="table":
                    out.append(f'<div class="el" style="{style}"><table>')
                    for ri,row in enumerate(e.rows):
                        fill=e.fill if ri==0 else scene.background
                        color=max(profile.colors,key=lambda v:contrast(v,fill)) if ri==0 else e.color
                        out.append(f'<tr style="height:{b.h/len(e.rows)}px;background:{fill};color:{color}">')
                        for cell in row:
                            wrapped="\n".join(wrap_text(cell,profile.font_file,e.size,b.w/len(row)-16))
                            out.append('<td>'+escape(wrapped)+'</td>')
                        out.append('</tr>')
                    out.append('</table></div>')
        out.append('</section>')
    out.append('</body></html>')
    path.write_text("".join(out))

def render_variant(scenes,profile,source,directory):
    rendered_text=[]
    for scene in scenes:
        for original in scene.elements:
            for element in primitives(original,profile):
                rendered_text.append(element.text)
                rendered_text.extend(cell for row in element.rows for cell in row)
    check_glyphs(profile.font_file,"\n".join(rendered_text))
    directory.mkdir(parents=True,exist_ok=True)
    render_pptx(scenes,profile,source,directory/"deck.pptx")
    render_pdf(scenes,profile,directory/"deck.pdf")
    render_html(scenes,profile,directory/"deck.html")
    (directory/"slides.json").write_text(json.dumps([s.model_dump() for s in scenes],ensure_ascii=False,indent=2))
    import pypdfium2 as pdfium
    # PDFium calls are serialized; its C API is not thread-safe.
    with _pdfium_lock, pdfium.PdfDocument(str(directory/"deck.pdf")) as doc:
        if len(doc)!=len(scenes):
            raise ValueError("Неверное число страниц PDF")
        for i in range(len(doc)):
            page=doc[i]
            bitmap=page.render(scale=1)
            bitmap.to_pil().save(directory/f"slide-{i+1}.png")
            bitmap.close();page.close()
