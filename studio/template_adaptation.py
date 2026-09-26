"""Reuse proven empty source composition regions for editable data visuals."""
from pathlib import Path
from PIL import Image, ImageChops
from .models import Box


def uniform_region(path, box, width, height):
    if not path or not Path(path).is_file():return False
    with Image.open(path) as image:
        image=image.convert('RGB')
        crop=image.crop((round(box.x/width*image.width),round(box.y/height*image.height),
            round((box.x+box.w)/width*image.width),round((box.y+box.h)/height*image.height)))
        if crop.width<1 or crop.height<1:return False
        colors=crop.getcolors(crop.width*crop.height)
        background=max(colors,key=lambda row:row[0])[1]
        diff=ImageChops.difference(crop,Image.new('RGB',crop.size,background))
        hist=diff.convert('L').histogram()
        return sum(hist[:10])/sum(hist)>=.995


def derive_data_patterns(profile):
    added=[]
    for pattern in list(profile.patterns):
        if not pattern.source_slide or not pattern.reusable or pattern.purpose in ('cover','divider','service','reference'):
            continue
        zones=pattern.body_zones
        if len(zones)<2 or max(z.x for z in zones)-min(z.x for z in zones)>24:continue
        all_zones=zones+[z for z in pattern.heading_zones+pattern.number_zones if z]
        left=min(z.x for z in all_zones);top=min(z.y for z in all_zones)
        box=Box(x=left,y=top,w=max(z.x+z.w for z in all_zones)-left,
            h=max(z.y+z.h for z in all_zones)-top)
        if box.h<190 or not uniform_region(pattern.background_image,box,profile.width,profile.height):continue
        # Grow only within the same measured empty band, bounded by canvas,
        # title and artwork. Short source labels need not cap a chart's width.
        margin=max(24,profile.margin)
        left_limit=margin
        title=pattern.title_zone
        if title and title.y<box.y+box.h and box.y<title.y+title.h:
            left_limit=max(left_limit,title.x+title.w+16)
        while box.x-8>=left_limit:
            probe=box.model_copy(update={'x':box.x-8,'w':box.w+8})
            if not uniform_region(pattern.background_image,probe,profile.width,profile.height):break
            box=probe
        while box.x+box.w+8<=profile.width-margin:
            probe=box.model_copy(update={'w':box.w+8})
            if not uniform_region(pattern.background_image,probe,profile.width,profile.height):break
            box=probe
        if box.w<260:continue
        derived=pattern.model_copy(deep=True);derived.id='data-'+pattern.id
        if any(p.id==derived.id for p in profile.patterns):continue
        derived.purpose='content';derived.role='content';derived.graphic_kind='none'
        derived.graphic_order_verified=False;derived.body_zones=[box]
        derived.heading_zones=[];derived.number_zones=[]
        derived.text_zones=[derived.title_zone,box] if derived.title_zone else [box]
        first=next((f for f in derived.fields if f['role']=='body' and f['index']==0),None)
        if not first:continue
        for field in derived.fields:
            if field is first:field['box']=box.model_dump()
            elif field['role'] in ('body','heading','number'):field['role']='unused'
        derived.zone_backgrounds=derived.zone_backgrounds[:1]
        derived.zone_foregrounds=derived.zone_foregrounds[:1]
        derived.safe_text_zone={**derived.safe_text_zone,
            'field_checks':[r for r in derived.safe_text_zone.get('field_checks',[]) if r.get('role')=='title']+
                [{'role':'body','index':0,'status':'safe','before':box.model_dump(),'after':box.model_dump(),
                  'reason':'uniform_sanitized_background'}],
            'data_region':box.model_dump(),
            'data_region_method':'authored_field_band_expanded_with_uniform_background_guard'}
        profile.patterns.append(derived);added.append({'id':derived.id,'source_pattern':pattern.id,'box':box.model_dump()})
    return added


def adapt_native_text_fields(profile):
    """Keep source artwork while repairing type on authored solid-colour plates."""
    from .template import contrast
    changes=[]
    for pattern in profile.patterns:
        if not pattern.source_slide or not pattern.reusable:continue
        checks=pattern.safe_text_zone.setdefault('field_checks',[])
        for field in pattern.fields:
            role=field['role'];index=field['index']
            if role not in ('title','body'):continue
            zone=pattern.title_zone if role=='title' else pattern.body_zones[index]
            original=zone.model_copy();safe=zone.model_copy()
            # Some organic text plates taper at the right edge. Only contract
            # the original viewport; never cover art with a new rectangle.
            while safe.w>=original.w*.8 and not uniform_region(pattern.background_image,safe,profile.width,profile.height):
                safe.w-=4
            if safe.w<original.w*.8:continue
            background=pattern.title_background if role=='title' else pattern.zone_backgrounds[index] if index<len(pattern.zone_backgrounds) else pattern.background
            foreground=pattern.title_foreground if role=='title' else pattern.zone_foregrounds[index] if index<len(pattern.zone_foregrounds) else pattern.foreground
            if not background or not foreground:continue
            color=foreground
            if contrast(color,background)<4.5:
                color=max(profile.colors,key=lambda c:contrast(c,background))
                if contrast(color,background)<4.5:continue
            size=field.get('font_size',0)
            readable=max(size,16) if role=='body' and safe.h>=20 else size
            if role=='title':pattern.title_zone=safe;pattern.title_foreground=color
            else:
                pattern.body_zones[index]=safe
                while len(pattern.zone_foregrounds)<=index:pattern.zone_foregrounds.append(pattern.foreground)
                pattern.zone_foregrounds[index]=color
            field['box']=safe.model_dump();field['font_size']=readable
            field.setdefault('style',{}).update(color=color,size=readable)
            checks[:]=[c for c in checks if not (c.get('role')==role and c.get('index')==index)]
            checks.append({'role':role,'index':index,'status':'safe','before':original.model_dump(),
                'after':safe.model_dump(),'reason':'authored_uniform_text_plate'})
            if color!=foreground or readable!=size or safe!=original:
                changes.append({'pattern_id':pattern.id,'role':role,'index':index,
                    'original_color':foreground,'color':color,'original_size':size,'size':readable,
                    'original_box':original.model_dump(),'box':safe.model_dump()})
    return changes


def derive_numbered_timelines(profile,source):
    """Turn proven sequential number fields into dated row headings, retaining art."""
    from .powerpoint import open_presentation
    from .template import walk_shapes
    import re
    prs=open_presentation(source);added=[]
    for pattern in list(profile.patterns):
        if not pattern.source_slide or not pattern.reusable or pattern.purpose!='agenda' or not 3<=len(pattern.body_zones)<=10:continue
        if max(z.x for z in pattern.body_zones)-min(z.x for z in pattern.body_zones)>16:continue
        texts={shape.shape_id:shape.text.strip() for shape,_ in walk_shapes(prs.slides[pattern.source_slide-1].shapes) if shape.has_text_frame}
        labels=[]
        for index,zone in enumerate(pattern.body_zones):
            matches=[f for f in pattern.fields if f['role']=='unused' and
                re.fullmatch(r'0*'+str(index+1),texts.get(f['shape_id'],'')) and
                abs(f['box']['y']-zone.y)<12 and f['box']['x']<zone.x]
            if len(matches)!=1:break
            labels.append(matches[0])
        if len(labels)!=len(pattern.body_zones):continue
        derived=pattern.model_copy(deep=True);derived.id='timeline-'+pattern.id
        if any(p.id==derived.id for p in profile.patterns):continue
        derived.purpose='timeline';derived.heading_zones=[];valid=True
        for i,zone in enumerate(derived.body_zones):
            # A 90pt label fits a year range at 16pt. Both fields stay inside
            # the existing row and the measured empty side of the composition.
            heading=Box(x=zone.x-104,y=zone.y,w=96,h=24)
            body=Box(x=zone.x,y=zone.y,w=profile.width-max(24,profile.margin)-zone.x,h=24)
            if any(not uniform_region(pattern.background_image,b,profile.width,profile.height) for b in (heading,body)):
                valid=False;break
            derived.body_zones[i]=body;derived.heading_zones.append(heading)
            for field in derived.fields:
                if field['role']=='body' and field['index']==i:
                    field['box']=body.model_dump();field['font_size']=16
                    field.setdefault('style',{})['size']=16
                elif field['shape_id']==labels[i]['shape_id']:
                    field.update(role='heading',index=i,box=heading.model_dump(),font_size=16)
                    field['style']={'family':profile.font,'size':16,'color':pattern.foreground}
        if not valid:continue
        derived.text_zones=[derived.title_zone]+derived.body_zones
        derived.safe_text_zone={**derived.safe_text_zone,'row_adapter':'verified_sequential_numbers_to_dates'}
        profile.patterns.append(derived);added.append({'id':derived.id,'source_pattern':pattern.id,'rows':len(labels)})
    return added


def reposition_derived_titles(profile):
    """Find a measured empty header band above adapted data/chronology fields."""
    changes=[]
    for pattern in profile.patterns:
        if not pattern.id.startswith(('data-','timeline-')) or not pattern.body_zones:continue
        if uniform_region(pattern.background_image,pattern.title_zone,profile.width,profile.height):continue
        margin=max(24,profile.margin);top=min(z.y for z in pattern.body_zones)
        height=min(64,top-margin-16)
        if height<36:continue
        x=margin;candidate=None
        while profile.width-margin-x>=260:
            probe=Box(x=x,y=margin,w=profile.width-margin-x,h=height)
            if uniform_region(pattern.background_image,probe,profile.width,profile.height):candidate=probe;break
            x+=8
        if candidate is None:continue
        old=pattern.title_zone.model_dump();pattern.title_zone=candidate
        for field in pattern.fields:
            if field['role']=='title':field['box']=candidate.model_dump()
        checks=pattern.safe_text_zone.setdefault('field_checks',[])
        checks[:]=[r for r in checks if r.get('role')!='title']
        checks.append({'role':'title','index':0,'status':'safe','before':old,'after':candidate.model_dump(),
            'reason':'uniform_header_band_above_authored_fields'})
        if pattern.id.startswith('data-') and len(pattern.body_zones)==1:
            box=pattern.body_zones[0].model_copy()
            while box.x-4>=margin:
                probe=box.model_copy(update={'x':box.x-4,'w':box.w+4})
                if not uniform_region(pattern.background_image,probe,profile.width,profile.height):break
                box=probe
            pattern.body_zones[0]=box
            for field in pattern.fields:
                if field['role']=='body' and field['index']==0:field['box']=box.model_dump()
            for row in checks:
                if row.get('role')=='body':row['after']=box.model_dump()
        pattern.text_zones=[pattern.title_zone]+pattern.body_zones
        changes.append({'pattern_id':pattern.id,'before':old,'after':candidate.model_dump()})
    return changes


def derive_roomy_text_patterns(profile):
    """Expand authored body fields within the same proven solid plate, retaining art/title."""
    added=[]
    for pattern in list(profile.patterns):
        if (not pattern.source_slide or not pattern.reusable or pattern.id.startswith(('roomy-','data-','timeline-'))
                or pattern.purpose not in ('content','unknown','context','summary')
                or pattern.graphic_kind!='none' or not pattern.body_zones):continue
        derived=pattern.model_copy(deep=True);derived.id='roomy-'+pattern.id
        if any(p.id==derived.id for p in profile.patterns):continue
        changes=[]
        for i,original in enumerate(pattern.body_zones):
            if not uniform_region(pattern.background_image,original,profile.width,profile.height):continue
            box=original.model_copy()
            obstacles=[z for j,z in enumerate(derived.body_zones) if j!=i]+[pattern.title_zone]
            obstacles += [z for z in pattern.heading_zones+pattern.number_zones if z]
            def safe(probe):
                if (probe.x<0 or probe.y<0 or probe.x+probe.w>max(profile.width-24,original.x+original.w)+.1
                        or probe.y+probe.h>max(profile.height-24,original.y+original.h)+.1):return False
                if any(z and probe.x<z.x+z.w+12 and z.x<probe.x+probe.w+12
                    and probe.y<z.y+z.h+12 and z.y<probe.y+probe.h+12 for z in obstacles):return False
                return uniform_region(pattern.background_image,probe,profile.width,profile.height)
            for direction in ('right','down','up'):
                for _ in range(24):
                    probe=box.model_copy()
                    if direction=='right':probe.w+=4
                    else:
                        probe.h+=4
                        if direction=='up':probe.y-=4
                    if probe.w>original.w*1.4 or probe.h>original.h*1.7 or not safe(probe):break
                    box=probe
            if box==original:continue
            derived.body_zones[i]=box
            for field in derived.fields:
                if field['role']=='body' and field['index']==i:field['box']=box.model_dump()
            checks=derived.safe_text_zone.setdefault('field_checks',[])
            checks[:]=[r for r in checks if not (r.get('role')=='body' and r.get('index')==i)]
            checks.append({'role':'body','index':i,'status':'safe','before':original.model_dump(),
                'after':box.model_dump(),'reason':'same_uniform_plate_expansion'})
            changes.append(i)
        if changes:
            derived.text_zones=[derived.title_zone]+derived.body_zones
            profile.patterns.append(derived);added.append({'id':derived.id,'source_pattern':pattern.id,'expanded_fields':changes})
    return added
