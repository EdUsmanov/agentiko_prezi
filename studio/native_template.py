"""Source geometry and native artwork, without copying source narrative into new slides."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
from hashlib import sha256
from pptx.enum.shapes import PP_PLACEHOLDER
from .models import Pattern, Box, SlideScene

P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

def box(shape):
    return Box(x=shape.left/12700, y=shape.top/12700, w=shape.width/12700, h=shape.height/12700)

def intersects(a,b):
    return min(a.x+a.w,b.x+b.w)-max(a.x,b.x)>2 and min(a.y+a.h,b.y+b.h)-max(a.y,b.y)>2

def native_patterns(prs):
    w,h = prs.slide_width/12700,prs.slide_height/12700
    layouts = {str(l.part.partname):(mi,li) for mi,m in enumerate(prs.slide_masters) for li,l in enumerate(m.slide_layouts)}
    surfaces = [(i+1,s,s.slide_layout) for i,s in enumerate(prs.slides)]
    surfaces += [(0,l,l) for m in prs.slide_masters for l in m.slide_layouts]
    result = []
    for number,surface,layout in surfaces:
        titles, bodies = [],[]
        for sh in surface.shapes:
            if not sh.is_placeholder:
                continue
            kind = sh.placeholder_format.type
            if kind in (PP_PLACEHOLDER.TITLE,PP_PLACEHOLDER.CENTER_TITLE):
                titles.append(sh)
            elif kind in (PP_PLACEHOLDER.BODY,PP_PLACEHOLDER.OBJECT,PP_PLACEHOLDER.SUBTITLE):
                bodies.append(sh)
        if number and not titles and not bodies:
            # Example decks may have no placeholders: use small, unambiguous text structures.
            text_shapes=[sh for sh in surface.shapes if sh.has_text_frame and sh.text.strip()
                and sh.width/12700>w*.18 and sh.height/12700>8 and sh.top/12700<h*.85]
            if 2<=len(text_shapes)<=4:
                ordered=sorted(text_shapes,key=lambda sh:(sh.top,sh.left))
                if ordered[0].top/12700<h*.25:
                    titles,bodies=ordered[:1],ordered[1:]
        if len(titles)!=1 or not 1<=len(bodies)<=3:
            continue
        title = box(titles[0]); zones = sorted([box(s) for s in bodies],key=lambda b:(b.y,b.x))
        if any(b.x<0 or b.y<0 or b.w<w*.18 or b.h<8 or b.x+b.w>w+1 or b.y+b.h>h+1 for b in [title]+zones):
            continue
        if any(b.h<h*.09 for b in zones):
            continue
        if any(intersects(a,b) for i,a in enumerate([title]+zones) for b in ([title]+zones)[i+1:]):
            continue
        # Picture-driven/team/grid layouts need a semantic image adapter, not empty photo slots.
        if any(sh.is_placeholder and sh.placeholder_format.type==PP_PLACEHOLDER.PICTURE for sh in surface.shapes):
            continue
        mi,li = layouts[str(layout.part.partname)]
        sizes = [r.font.size.pt for p in titles[0].text_frame.paragraphs for r in p.runs if r.font.size]
        result.append(Pattern(id=f"native-slide-{number}" if number else f"native-layout-{mi}-{li}",
            source_slide=number,source_layout=layout.name,master_index=mi,layout_index=li,
            title_zone=title,body_zones=zones,text_zones=[title]+zones,
            title_size=max(sizes,default=0),role="split" if len(zones)==2 else "columns" if len(zones)==3 else "statement"))
    return result

def scrub_surface(surface):
    # Masters/layouts keep their artwork and geometry; sample placeholder copy is not content.
    for node in surface._element.iter(A+"t"):
        node.text = ""
    for node in list(surface._element.iter()):
        if node.tag in (A+"hlinkClick",A+"hlinkMouseOver"):
            node.getparent().remove(node)

def copy_node(node, source_part, target_part):
    copied = deepcopy(node)
    for child in list(copied.iter()):
        if child.tag in (A+"hlinkClick",A+"hlinkMouseOver"):
            child.getparent().remove(child)
            continue
        for attr,value in list(child.attrib.items()):
            if not attr.startswith(R):
                continue
            rel = source_part.rels.get(value)
            if rel and not rel.is_external and rel.reltype.endswith("/image"):
                child.set(attr,target_part.relate_to(rel.target_part,rel.reltype))
            else:
                del child.attrib[attr]
    return copied

def source_slide(prs, pattern):
    layout = prs.slide_masters[pattern.master_index].slide_layouts[pattern.layout_index]
    slide = prs.slides.add_slide(layout)
    for sh in list(slide.shapes):
        sh._element.getparent().remove(sh._element)
    if pattern.source_slide:
        original = prs._studio_sources[pattern.source_slide-1]
        bg = original._element.find(P+"cSld/"+P+"bg")
        if bg is not None:
            slide._element.cSld.insert(0,copy_node(bg,original.part,slide.part))
        if "showMasterSp" in original._element.attrib:
            slide._element.set("showMasterSp",original._element.get("showMasterSp"))
        for sh in original.shapes:
            # Ordinary slide pictures/charts may be prior content. Do not silently reuse them.
            if sh.is_placeholder or sh.has_chart or sh.has_table:
                continue
            if hasattr(sh,"image") and sha256(sh.image.blob).hexdigest()[:20] not in prs._studio_brand_hashes:
                continue
            if any((n.text or "").strip() for n in sh._element.iter(A+"t")):
                continue
            slide.shapes._spTree.insert_element_before(copy_node(sh._element,original.part,slide.part),"p:extLst")
    return slide

def compile_backgrounds(profile, source, directory):
    """Preparation has no five-minute limit: render sanitized artwork once and cache it."""
    from .office import executable, to_pdf
    from .render import render_pptx, _pdfium_lock
    from .template import contrast
    if not executable() or not profile.patterns:
        profile.warnings.append("LibreOffice недоступен: фон HTML/PDF может отличаться от PPTX; требуется визуальная проверка.")
        return
    folder = Path(directory)/"template-layers"; folder.mkdir(exist_ok=True)
    scenes = [SlideScene(title="",background=profile.background,elements=[],source_ids=[],layout=p.role,
        pattern_id=p.id,strategy="native_template") for p in profile.patterns]
    render_pptx(scenes,profile,source,folder/"artwork.pptx",verify_text=False)
    to_pdf(folder/"artwork.pptx",folder,timeout=120,font_file=profile.font_file)
    import pypdfium2 as pdfium
    with _pdfium_lock, pdfium.PdfDocument(str(folder/"artwork.pdf")) as doc:
        if len(doc)!=len(profile.patterns):
            raise ValueError("Неверное число отрисованных шаблонных паттернов")
        for i,pattern in enumerate(profile.patterns):
            page=doc[i]; bitmap=page.render(scale=1.5); im=bitmap.to_pil()
            target=folder/(pattern.id+".png"); im.save(target)
            pattern.background_image=str(target)
            # Use representative pixels INSIDE the actual body zone, not palette contrast guesses.
            rgb=im.convert("RGB")
            backgrounds=[]; foregrounds=[]
            for zone in [pattern.title_zone]+pattern.body_zones:
                samples=[rgb.getpixel((min(im.width-1,int((zone.x+zone.w*x)*1.5)),
                    min(im.height-1,int((zone.y+zone.h*y)*1.5)))) for x in (.2,.5,.8) for y in (.2,.5,.8)]
                color=Counter(samples).most_common(1)[0][0]
                bg="#%02X%02X%02X"%color
                foregrounds.append(max(profile.colors,key=lambda c:contrast(c,bg)))
                backgrounds.append(bg)
            pattern.title_background=backgrounds[0];pattern.title_foreground=foregrounds[0]
            pattern.zone_backgrounds=backgrounds[1:];pattern.zone_foregrounds=foregrounds[1:]
            pattern.background=backgrounds[1];pattern.foreground=foregrounds[1]
            profile.colors=list(dict.fromkeys(profile.colors+backgrounds))
            bitmap.close();page.close()
