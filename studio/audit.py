import re
from .models import Finding
from .fonts import wrap_text, element_font, table_cell_fits
from .template import contrast
from .table_style import column_widths

def overlaps(a,b):
    return min(a.x+a.w,b.x+b.w)-max(a.x,b.x)>2 and min(a.y+a.h,b.y+b.h)-max(a.y,b.y)>2

def audit_scenes(scenes, package):
    from .sections import divider_members
    chapters=divider_members(package)
    p=package.template
    from .quality import scene_quality_findings
    findings=scene_quality_findings(scenes, package)
    def add(code,msg,slide=0,element=None,severity="error"):
        findings.append(Finding(code=code,severity=severity,message=msg,slide=slide,element=element))
    from .storyboard import planned_slide_count
    adjusted = package.analysis.get('slide_budget', {}).get('status') == 'adjusted'
    target = planned_slide_count(package) if adjusted else package.constraints.slides
    if len(scenes)>target or (package.constraints.count_mode!="maximum" and len(scenes)!=target) or adjusted and len(scenes)!=target:
        add("slide_count","Недостаточно исходных фактов для заданного числа содержательных слайдов")
    elif adjusted:
        add('slide_count_adjusted', package.analysis['slide_budget']['message'], severity='warning')
    expected={f.id for f in package.content.facts}
    used=set()
    seen=set()
    for si,s in enumerate(scenes,1):
        pattern=next((p for p in package.template.patterns if p.id==s.pattern_id),None)
        from .contracts import normalized, compatible
        from .models import SlidePlan
        required = package.analysis.get('storyboard', [])
        if pattern and (not pattern.reusable or pattern.purpose in ('service','reference')):
            add('layout_purpose','Служебный или справочный слайд шаблона использован как контент',si)
        if len(required)==len(scenes):
            contract=SlidePlan.model_validate(required[si-1])
            if s.purpose != 'auto' and s.purpose != contract.purpose:
                add('archetype_changed','Архетип слайда изменён после построения сценария',si)
            if pattern and not compatible(pattern,contract,si-1):
                add('layout_purpose','Назначение макета нарушает сценарий',si)
            if (s.layout=='divider') != (contract.layout=='divider'):
                add('missing_divider','Нарушено положение разделителя в сценарии',si)
            if (pattern and contract.purpose not in ('auto', 'content', 'cover', 'divider')
                    and pattern.purpose in ('unknown', 'content')):
                add('archetype_generic_layout','Смысловой тип сохранён, но выбран общий макет: проверьте представление содержания',si,severity='info')
        if any(e.kind=='text' and e.role not in ('title','footer') and normalized(e.text)==normalized(s.title) for e in s.elements):
            add('duplicate_title','Заголовок повторяется в основном тексте',si)
        for table in package.content.tables:
            source_ids={f.id for f in package.content.facts if f.source==table.id}
            represented=[e for e in s.elements if set(e.source_ids)&source_ids]
            if represented and table.visualization=='metrics':
                if not any(e.role=='metric_value' for e in represented):
                    add('visualization_intent','Запрошенные карточки показателей не воспроизведены',si)
            elif represented and table.visualization not in ('auto','table'):
                if not any(e.kind=='chart' and e.chart_type==table.visualization for e in represented):
                    add('visualization_intent','Запрошенный вид диаграммы не воспроизведён; таблица не считается его эквивалентом',si)
        # Scene metadata/notes are not visible content and cannot prove coverage.
        used.update(fid for e in s.elements for fid in e.source_ids)
        key=tuple(sorted(s.source_ids))
        if key in seen and s.layout!="divider":
            add("duplicate_slide","Повторяется набор исходных фактов",si,severity="warning")
        seen.add(key)
        if s.layout=="divider":
            if not pattern or pattern.role!="divider" or s.title not in chapters:
                add("invalid_divider","Разделитель не соответствует исходному разделу или шаблону",si)
        elif not any(e.source_ids for e in s.elements) and not (pattern and pattern.role=='cover'):
            add("empty_slide","Слайд без исходных фактов",si)
        for ei,e in enumerate(s.elements):
            if e.role=="template_background":
                continue
            b=e.box
            if pattern and s.strategy=="native_template" and e.kind=="text" and e.role!="label":
                zones=([pattern.title_zone] if e.role=="title" else pattern.body_zones+[z for z in pattern.heading_zones if z])
                if zones and not any(z and b.x>=z.x-.5 and b.y>=z.y-.5 and b.x+b.w<=z.x+z.w+.5 and b.y+b.h<=z.y+z.h+.5 for z in zones):
                    add("container_overflow","Текст выходит за свою область в исходном макете",si,ei)
            if b.w<=0 or b.h<=0 or b.x < -.5 or b.y < -.5 or b.x+b.w > p.width+.5 or b.y+b.h > p.height+.5:
                add("out_of_bounds","Объект выходит за границу слайда",si,ei)
            if e.color and e.color not in p.colors or e.fill and e.fill not in p.colors:
                add("palette","Цвет отсутствует в шаблоне",si,ei)
            if e.font and e.font not in p.fonts:
                add("font","Шрифт отсутствует в шаблоне",si,ei)
            if e.kind=="text":
                lines=wrap_text(e.text,element_font(p,e)[1],e.size,(b.w-(e.size*1.4 if e.bullet else 0))*(.94 if e.bold or e.bold_prefix else 1))
                if len(lines)*e.size*1.25 > b.h+.5:
                    add("text_overflow","Текст не помещается; увеличьте число слайдов или сократите материал",si,ei)
                if contrast(e.color,e.background_hint or s.background)<4.5:
                    add("contrast","Контраст текста меньше 4.5:1",si,ei,severity="warning")
                if re.search(r"\b(?:lorem ipsum|TODO|XXX)\b|вставьте текст",e.text,re.I):
                    add("placeholder","В исходном материале осталась заглушка",si,ei,severity="warning")
            if e.kind=="table":
                row_h=b.h/len(e.rows)
                widths=column_widths(e.rows,b.w,element_font(p,e)[1],e.size)
                for ri,row in enumerate(e.rows):
                    for ci,cell in enumerate(row):
                        if not table_cell_fits(cell,element_font(p,e)[1],e.size,widths[ci]-16,row_h-12,ri==0):
                            add("table_overflow","Ячейки таблицы не помещаются",si,ei)
                            break
                if len(e.rows)>8 or len(e.rows[0])>5:
                    add("table_density","Таблица плотнее ориентира 7 строк × 5 колонок",si,ei,severity="warning")
            if e.kind=="chart":
                from .render import chart_fits
                if not chart_fits(e,p):
                    add("chart_overflow","Подписи или столбцы графика не помещаются",si,ei)
        for ei,e in enumerate(s.elements):
            if e.role=="template_background":
                continue
            if e.kind not in ("text","table","chart","image"):
                continue
            for other in s.elements[ei+1:]:
                if other.role!="template_background" and other.kind in ("text","table","chart","image") and overlaps(e.box,other.box):
                    add("overlap","Пересечение содержательных блоков",si,ei)
    if used != expected:
        add("coverage","Не все исходные факты представлены в презентации")
    # Ignore single-slide geometry probes; full-deck audits enforce all image assets.
    if len(scenes)==planned_slide_count(package):
        expected_images={a.id for a in package.images}
        actual_images=[e.image_id for s in scenes for e in s.elements if e.image_id]
        if set(actual_images)!=expected_images or len(actual_images)!=len(expected_images):
            add("image_coverage","Не все загруженные изображения размещены ровно один раз")
        by_id={a.id:a for a in package.images}
        for scene in scenes:
            for element in scene.elements:
                if element.image_id and (element.image_id not in by_id or element.image_path!=by_id[element.image_id].path):
                    add("image_source","Неизвестный источник изображения")
    return findings

def repair_scenes(scenes,package):
    repairs=[]
    for si,s in enumerate(scenes,1):
        for ei,e in enumerate(s.elements):
            if e.kind=="chart":
                from .render import chart_fits
                if not chart_fits(e,package.template):
                    sources={f.source for f in package.content.facts if f.id in e.source_ids}
                    table=next((t for t in package.content.tables if t.id in sources),None)
                    if table:
                        e=e.model_copy(update={"kind":"table","rows":[table.headers]+table.rows})
                        from .table_style import apply_table_style
                        apply_table_style(e,next((p for p in package.template.patterns if p.id==s.pattern_id),None))
                        s.elements[ei]=e
                        repairs.append(Finding(code="chart_to_table",severity="info",message="График заменён редактируемой таблицей: подписи не помещались; значения сохранены",slide=si,element=ei,repaired=True))
            if e.kind=="table":
                for size in sorted({e.size, *[x for x in package.template.font_sizes if 10 <= x <= e.size]},reverse=True):
                    row_h=e.box.h/len(e.rows)
                    widths=column_widths(e.rows,e.box.w,element_font(package.template,e)[1],size)
                    if all(table_cell_fits(cell,element_font(package.template,e)[1],size,widths[ci]-16,row_h-12,ri==0) for ri,row in enumerate(e.rows) for ci,cell in enumerate(row)):
                        if size!=e.size:
                            e.size=size
                            repairs.append(Finding(code="table_fit",severity="info",message="Размер текста таблицы скорректирован в пределах шкалы шаблона",slide=si,element=ei,repaired=True))
                        break
    from .table_style import compact_table
    for scene in scenes:
        for element in scene.elements:
            compact_table(element,package.template)
    return repairs
