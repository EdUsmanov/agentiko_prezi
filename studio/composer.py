from .models import Box, Element, SlideScene
from .fonts import wrap_text
from .content import numeric_column
from .template import contrast

def text_element(text, box, profile, role="body", size=None, color=None, source_ids=None):
    size = size or profile.body_size
    candidates = [s for s in profile.font_sizes if 10 <= s <= size]
    candidates = sorted(set(candidates + [size]), reverse=True)
    for candidate in candidates:
        if len(wrap_text(text, profile.font_file, candidate, box.w)) * candidate * 1.25 <= box.h:
            return Element(kind="text", box=box, text=text, font=profile.font, size=candidate,
                color=color or profile.foreground, bold=role == "title", role=role, source_ids=source_ids or [])
    # Preserve the text; audit will surface overflow rather than silently truncate.
    return Element(kind="text", box=box, text=text, font=profile.font, size=min(candidates),
        color=color or profile.foreground, role=role, source_ids=source_ids or [])

def compose(slide, package, index, variant):
    native = compose_native(slide,package,index,variant)
    if native is not None:
        return native
    p = package.template
    w, h, m = p.width, p.height, p.margin
    facts = {f.id: f for f in package.content.facts}
    relevant = [facts[i] for i in slide.fact_ids]
    tables = {t.id: t for t in package.content.tables}
    elements = []
    top_assets = [a for a in p.assets if a.box.y < h*.15]
    title_y = max([m] + [a.box.y + a.box.h + 12 for a in top_assets])
    title_h = min(h*.23, max(p.title_size*1.3, len(wrap_text(slide.title, p.font_file, p.title_size, w-2*m))*p.title_size*1.25))
    elements.append(text_element(slide.title, Box(x=m,y=title_y,w=w-2*m,h=title_h), p, "title", p.title_size))
    y = title_y + title_h + h*.055
    bottom = min([h-m] + [a.box.y-12 for a in p.assets if a.box.y > h*.8])
    content_h = bottom-y
    if content_h < h*.3:
        raise ValueError("Шаблон не оставляет достаточно места для заголовка и содержания")
    gap = w*.025
    # Every fact is rendered, or represented by its full source table.
    body = [f for f in relevant if f.source not in tables]
    if slide.table_id:
        table = tables[slide.table_id]
        numeric = numeric_column(table)
        other_tables = [f for f in relevant if f.source in tables and f.source != slide.table_id]
        if other_tables:
            raise ValueError("Несколько таблиц на одном слайде: увеличьте число слайдов")
        visual_w = w-2*m if not body else (w-2*m)*.62
        table_x=m
        if variant=="story":
            visual_w*=.86
            if not body:
                table_x=m+(w-2*m-visual_w)/2
        elif variant=="executive" and not body and slide.layout!="chart":
            visual_w*=.94
        box = Box(x=table_x,y=y,w=visual_w,h=content_h)
        if slide.layout == "chart" and numeric:
            column, values, unit = numeric
            elements.append(Element(kind="chart", box=box, labels=[r[0] for r in table.rows], values=values,
                unit=table.headers[column] + (f" ({unit})" if unit else ""), font=p.font, size=p.body_size,
                color=p.foreground, fill=p.accent, source_ids=[f.id for f in relevant if f.source==table.id]))
            # Charts must not hide other columns from the source table.
            if len(table.headers)>2:
                elements[-1] = Element(kind="table", box=box, rows=[table.headers]+table.rows,
                    font=p.font,size=p.body_size,color=p.foreground,fill=p.accent,
                    source_ids=[f.id for f in relevant if f.source==table.id])
        else:
            elements.append(Element(kind="table", box=box, rows=[table.headers]+table.rows,
                font=p.font, size=p.body_size, color=p.foreground, fill=p.accent,
                source_ids=[f.id for f in relevant if f.source==table.id]))
        if body:
            x=m+visual_w+gap
            elements.append(text_element("\n\n".join(f.text for f in body), Box(x=x,y=y,w=w-m-x,h=content_h), p, source_ids=[f.id for f in body]))
    elif slide.layout == "columns" and len(body)>1:
        columns = min(len(body), 3)
        col_w = (w-2*m-gap*(columns-1))/columns
        for c in range(columns):
            group = body[c*len(body)//columns:(c+1)*len(body)//columns]
            elements.append(Element(kind="line",box=Box(x=m+c*(col_w+gap), y=y,w=col_w,h=1),color=p.accent))
            elements.append(text_element("\n\n".join(f.text for f in group),Box(x=m+c*(col_w+gap),y=y+14,w=col_w,h=content_h-14),p,source_ids=[f.id for f in group]))
    elif slide.layout == "split" and len(body)>1:
        left_w=(w-2*m-gap)*.48
        elements.append(text_element(body[0].text,Box(x=m,y=y,w=left_w,h=content_h),p,
            size=min(p.font_sizes,key=lambda s:abs(s-p.body_size*1.3)),source_ids=[body[0].id]))
        elements.append(text_element("\n\n".join(f.text for f in body[1:]),Box(x=m+left_w+gap,y=y,w=w-2*m-left_w-gap,h=content_h),p,source_ids=[f.id for f in body[1:]]))
    elif slide.layout == "evidence":
        # Flat rows: evidence references on the left, complete source text on the right.
        row_h=content_h/max(len(body),1)
        for i,f in enumerate(body):
            label_color=p.accent if contrast(p.accent,p.background)>=4.5 else p.foreground
            elements.append(text_element(f.text,Box(x=m+w*.1,y=y+i*row_h,w=w-2*m-w*.1,h=row_h-12),p,source_ids=[f.id]))
    elif slide.layout == "process" and len(body)>=2:
        row_h=content_h/len(body)
        for i,f in enumerate(body):
            elements.append(text_element(str(i+1),Box(x=m,y=y+i*row_h,w=w*.07,h=row_h-8),p,"label"))
            elements.append(text_element(f.text,Box(x=m+w*.1,y=y+i*row_h,w=w-2*m-w*.1,h=row_h-8),p,source_ids=[f.id]))
    else:
        text="\n\n".join(f.text for f in body)
        size=p.body_size
        text_w=w-2*m
        if slide.layout=="statement":
            size=min(p.font_sizes,key=lambda s:abs(s-p.body_size*1.6))
            text_w*=.92
        elif slide.layout=="split":
            # Single-fact variant: different composition without duplicated evidence.
            text_w*=.72
        elements.append(text_element(text,Box(x=m,y=y,w=text_w,h=content_h),p,size=size,source_ids=[f.id for f in body]))
    footer_size=min(p.font_sizes,key=lambda s:abs(s-10))
    elements.append(text_element(f"{index+1:02d}",Box(x=w-m-35,y=h-max(m*.6,footer_size*1.5+4),w=35,h=footer_size*1.5),p,"footer",footer_size))
    for asset in p.assets:
        elements.append(Element(kind="image",box=asset.box,image_path=asset.path,role="brand"))
    # Adapt safe text zones from an observed ordinary slide when they fit.
    # Otherwise use the token-derived composition above. No source wording is copied.
    body_elements=[e for e in elements if e.kind=="text" and e.role=="body"]
    pattern_id=None
    if slide.layout in ("split","columns") and len(body_elements) in (2,3):
        for pattern in p.patterns:
            zones=[b for b in pattern.text_zones if b.y>=y and b.y+b.h<=bottom+1 and b.x>=m*.8 and b.x+b.w<=w-m*.8 and b.w>=w*.2 and b.h>=h*.2]
            if len(zones)!=len(body_elements):
                continue
            from .audit import overlaps
            if any(overlaps(a,b) for i,a in enumerate(zones) for b in zones[i+1:]):
                continue
            zones.sort(key=lambda b:(b.x,b.y))
            if all(len(wrap_text(e.text,p.font_file,e.size,b.w))*e.size*1.25<=b.h for e,b in zip(body_elements,zones)):
                for e,b in zip(body_elements,zones):
                    e.box=b
                pattern_id=pattern.id
                break
    return SlideScene(title=slide.title,background=p.background,elements=elements,source_ids=slide.fact_ids,layout=slide.layout,
        pattern_id=pattern_id,strategy="exemplar_zones" if pattern_id else "token_composition",
        notes="\n".join(f"[{f.id}] {f.source}, строка {f.line}: {f.text}" for f in relevant))

def compose_variant(variant, package):
    return [compose(s,package,i,variant.key) for i,s in enumerate(variant.slides)]

def compose_native(slide, package, index, variant):
    """Fit editable content into source zones; never substitute a generic full-slide design."""
    p=package.template
    patterns=[pattern for pattern in p.patterns if pattern.title_zone and pattern.body_zones]
    if not patterns:
        return None
    facts={f.id:f for f in package.content.facts}; tables={t.id:t for t in package.content.tables}
    relevant=[facts[fid] for fid in slide.fact_ids]
    body=[f for f in relevant if f.source not in tables]
    if len({f.source for f in relevant if f.source in tables})>1:
        raise ValueError("Несколько таблиц на одном слайде: увеличьте число слайдов")
    desired=2 if len(body)>1 and slide.layout in ("split","columns") else 1
    if slide.table_id:
        desired=2 if body else 1

    def zones_for(pattern):
        zones=pattern.body_zones
        if len(zones)==1 and desired==2 and zones[0].w>p.width*.65:
            b=zones[0]; gap=p.width*.025
            ratio=.6 if slide.table_id else .4 if slide.layout=="split" else .5
            left=(b.w-gap)*ratio
            return [Box(x=b.x,y=b.y,w=left,h=b.h),Box(x=b.x+left+gap,y=b.y,w=b.w-left-gap,h=b.h)]
        return zones

    def elements_for(pattern):
        foreground=pattern.foreground or p.foreground
        title=text_element(slide.title,pattern.title_zone,p,"title",pattern.title_size or p.title_size,color=pattern.title_foreground or foreground)
        title.background_hint=pattern.title_background
        elements=[title]; zones=zones_for(pattern)
        if slide.table_id:
            table=tables[slide.table_id]; b=zones[0]
            numeric=numeric_column(table)
            if slide.layout=="chart" and numeric and len(table.headers)==2:
                col,values,unit=numeric
                elements.append(Element(kind="chart",box=b,labels=[r[0] for r in table.rows],values=values,
                    unit=table.headers[col]+(f" ({unit})" if unit else ""),font=p.font,size=p.body_size,
                    color=foreground,fill=p.accent,source_ids=[f.id for f in relevant if f.source==table.id]))
            else:
                elements.append(Element(kind="table",box=b,rows=[table.headers]+table.rows,font=p.font,
                    size=p.body_size,color=foreground,fill=p.accent,source_ids=[f.id for f in relevant if f.source==table.id]))
            if body:
                if len(zones)<2:
                    return None
                elements.append(text_element("\n\n".join(f.text for f in body),zones[1],p,color=foreground,source_ids=[f.id for f in body]))
        else:
            count=min(len(zones),len(body))
            for i,b in enumerate(zones[:count]):
                if slide.layout=="split" and count==2:
                    group=body[:1] if i==0 else body[1:]
                else:
                    group=body[i*len(body)//count:(i+1)*len(body)//count]
                elements.append(text_element("\n\n".join(f.text for f in group),b,p,color=foreground,source_ids=[f.id for f in group]))
        for e in elements[1:]:
            zone_index=next((i for i,b in enumerate(pattern.body_zones) if b.x<=e.box.x+1 and b.y<=e.box.y+1 and b.x+b.w>=e.box.x+e.box.w-1),0)
            if pattern.zone_backgrounds:
                e.background_hint=pattern.zone_backgrounds[zone_index]
                e.color=pattern.zone_foregrounds[zone_index]
        return elements

    options=[]
    for pattern in patterns:
        elements=elements_for(pattern)
        if elements is None:
            continue
        overflow=sum(max(0,len(wrap_text(e.text,p.font_file,e.size,e.box.w))*e.size*1.25-e.box.h) for e in elements if e.kind=="text")
        small=sum(max(0,p.body_size-e.size) for e in elements if e.role=="body" and e.kind=="text")
        count_penalty=abs(len(zones_for(pattern))-desired)*10
        # Prioritize readability, then matching template examples, then requested composition.
        score=overflow*100+small*2+count_penalty+(0 if pattern.source_slide else 3)
        options.append((score,pattern,elements))
    if not options:
        return None
    options.sort(key=lambda item:item[0])
    best=options[0][0]
    equivalent=[item for item in options if item[0]<=best+1]
    offset={"executive":0,"analytical":1,"story":2}[variant]
    _,pattern,elements=equivalent[(index+offset)%len(equivalent)]
    if pattern.background_image:
        elements.insert(0,Element(kind="image",box=Box(x=0,y=0,w=p.width,h=p.height),
            image_path=pattern.background_image,role="template_background"))
    return SlideScene(title=slide.title,background=pattern.background or p.background,elements=elements,
        source_ids=slide.fact_ids,layout=slide.layout,pattern_id=pattern.id,strategy="native_template",
        notes="\n".join(f"[{f.id}] {f.source}, строка {f.line}: {f.text}" for f in relevant))
