import re
from .models import Finding
from .fonts import wrap_text
from .template import contrast

def overlaps(a,b):
    return min(a.x+a.w,b.x+b.w)-max(a.x,b.x)>2 and min(a.y+a.h,b.y+b.h)-max(a.y,b.y)>2

def audit_scenes(scenes, package):
    p=package.template
    findings=[]
    def add(code,msg,slide=0,element=None,severity="error"):
        findings.append(Finding(code=code,severity=severity,message=msg,slide=slide,element=element))
    if len(scenes)>package.constraints.slides or (package.constraints.count_mode!="maximum" and len(scenes)!=package.constraints.slides):
        add("slide_count","Недостаточно исходных фактов для заданного числа содержательных слайдов")
    expected={f.id for f in package.content.facts}
    used=set()
    seen=set()
    for si,s in enumerate(scenes,1):
        used.update(s.source_ids)
        key=tuple(sorted(s.source_ids))
        if key in seen:
            add("duplicate_slide","Повторяется набор исходных фактов",si,severity="warning")
        seen.add(key)
        if not any(e.source_ids for e in s.elements):
            add("empty_slide","Слайд без исходных фактов",si)
        for ei,e in enumerate(s.elements):
            if e.role=="template_background":
                continue
            b=e.box
            if b.x < -.5 or b.y < -.5 or b.x+b.w > p.width+.5 or b.y+b.h > p.height+.5:
                add("out_of_bounds","Объект выходит за границу слайда",si,ei)
            if e.color and e.color not in p.colors or e.fill and e.fill not in p.colors:
                add("palette","Цвет отсутствует в шаблоне",si,ei)
            if e.font and e.font not in p.fonts:
                add("font","Шрифт отсутствует в шаблоне",si,ei)
            if e.kind=="text":
                lines=wrap_text(e.text,p.font_file,e.size,b.w)
                if len(lines)*e.size*1.25 > b.h+.5:
                    add("text_overflow","Текст не помещается; увеличьте число слайдов или сократите материал",si,ei)
                if contrast(e.color,e.background_hint or s.background)<4.5:
                    add("contrast","Контраст текста меньше 4.5:1",si,ei,severity="warning")
                if re.search(r"\b(?:lorem ipsum|TODO|XXX)\b|вставьте текст",e.text,re.I):
                    add("placeholder","В исходном материале осталась заглушка",si,ei,severity="warning")
            if e.kind=="table":
                row_h=b.h/len(e.rows)
                for row in e.rows:
                    for cell in row:
                        if len(wrap_text(cell,p.font_file,e.size,b.w/len(row)-16))*e.size*1.25>row_h-12:
                            add("table_overflow","Ячейки таблицы не помещаются",si,ei)
                            break
                if len(e.rows)>8 or len(e.rows[0])>5:
                    add("table_density","Таблица плотнее ориентира 7 строк × 5 колонок",si,ei,severity="warning")
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
    return findings

def repair_scenes(scenes,package):
    repairs=[]
    for si,s in enumerate(scenes,1):
        for ei,e in enumerate(s.elements):
            if e.kind=="table":
                for size in sorted([x for x in package.template.font_sizes if 10 <= x <= e.size],reverse=True):
                    row_h=e.box.h/len(e.rows)
                    if all(len(wrap_text(cell,package.template.font_file,size,e.box.w/len(row)-16))*size*1.25<=row_h-12 for row in e.rows for cell in row):
                        if size!=e.size:
                            e.size=size
                            repairs.append(Finding(code="table_fit",severity="info",message="Размер текста таблицы скорректирован в пределах шкалы шаблона",slide=si,element=ei,repaired=True))
                        break
    return repairs
