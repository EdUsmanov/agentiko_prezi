"""Count-aware, grounded editorial planning from immutable user evidence."""
import re
from collections import Counter
from typing import Literal
from pydantic import Field
from .models import StrictModel, Fact, TableData, SlidePlan, VariantPlan
from .content import SLIDE_RANGES, numeric_column


class Excerpt(StrictModel):
    fact_id: str
    quotes: list[str] = Field(min_length=1, max_length=12)


class DataRow(StrictModel):
    fact_id: str
    label: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=100)


class NarrativeSlide(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    excerpts: list[Excerpt] = Field(min_length=1, max_length=300)
    relationship: Literal['none', 'comparison', 'time', 'share', 'table'] = 'none'
    rows: list[DataRow] = Field(default_factory=list, max_length=20)


class Narrative(StrictModel):
    slides: list[NarrativeSlide] = Field(min_length=1, max_length=30)


def numbers(text):
    return Counter(re.findall(r'[-−+]?\d+(?:[.,]\d+)?\s*%?', text))


def validate_narrative(raw, content):
    parsed = Narrative.model_validate(raw)
    facts = {f.id:f for f in content.facts}
    ids = [e.fact_id for slide in parsed.slides for e in slide.excerpts]
    if ids != list(facts):
        raise ValueError('Every source fact must occur once, in source order')
    for slide in parsed.slides:
        evidence = ' '.join(facts[e.fact_id].text for e in slide.excerpts)
        if numbers(slide.title) - numbers(evidence):
            raise ValueError('Unsupported title number')
        tables = [facts[e.fact_id].source for e in slide.excerpts if facts[e.fact_id].source != 'user_text']
        if len(tables)>1 or tables and slide.rows:
            raise ValueError('One complete source table per slide')
        for e in slide.excerpts:
            fact = facts[e.fact_id]
            cursor=0
            for quote in e.quotes:
                pos=fact.text.find(quote,cursor)
                if not quote.strip() or pos<0:
                    raise ValueError('Summary must use ordered verbatim excerpts')
                cursor=pos+len(quote)
            summary=' … '.join(e.quotes)
            units=r'(?i)\b(?:млн|млрд|тыс|руб|доллар\w*|евро|кг|км|человек|сотрудник\w*)\b|[₽$€%]'
            if Counter(re.findall(units,fact.text)) != Counter(re.findall(units,summary)):
                raise ValueError('Measurement units must survive the summary')
            if numbers(fact.text) != numbers(summary):
                raise ValueError('All original numbers and units must survive the summary')
            # A shortened qualification can invert a claim despite literal quotes.
            if (fact.source!='user_text' or fact.kind=='caveat' or re.search(
                    r'\b(?:не|нет|без|если|кроме|только|менее|более|пример|условн\w*|not|unless|except)\b',fact.text,re.I)) and summary!=fact.text:
                raise ValueError('Keep qualified statements and source tables intact')
        allowed={e.fact_id for e in slide.excerpts}
        for row in slide.rows:
            if row.fact_id not in allowed or row.label not in facts[row.fact_id].text or row.value not in facts[row.fact_id].text:
                raise ValueError('Data labels and values must quote the same source fact')
        if slide.rows and (len(slide.rows)<2 or slide.relationship=='none'):
            raise ValueError('A quantitative comparison needs at least two grounded rows')
        if len({r.label for r in slide.rows})!=len(slide.rows):
            raise ValueError('Ambiguous duplicate categories')
    from .security_gate import check_text_fields
    check_text_fields(narrative=parsed.model_dump_json())
    return parsed.model_dump()


def choose_visualization(table, relationship):
    numeric=numeric_column(table)
    if not numeric or relationship=='table':
        return 'table'
    _,values,unit=numeric
    if relationship=='time':
        # Only recognisable dates can claim a temporal axis.
        if all(re.search(r'\d{4}|квартал|месяц|январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр|Q[1-4]',r[0],re.I) for r in table.rows):
            return 'line'
        return 'table'
    if relationship=='share' and unit=='%' and abs(sum(values)-100)<.01 and len(values)<=5:
        return 'pie'
    return 'bar' if max(map(len,(r[0] for r in table.rows)))>14 else 'column'


async def prepare_narrative(package,gateway,progress=None):
    if not package.constraints.summarize:
        return False
    if gateway.settings.mode!='api':
        package.analysis.setdefault('warnings',[]).append('Модель отключена: смысловое сокращение недоступно; исходный текст сохранён.')
        return False
    from .editorial import prepare_editorial
    return await prepare_editorial(package,gateway,progress)


def narrative_storyboard(package):
    from .storyboard import fit_storyboard
    facts={f.id:f for f in package.content.facts}
    tables={t.id:t for t in package.content.tables}
    outline=[]
    for group in package.analysis['narrative']['groups']:
        ids=group['fact_ids'];tid=next((facts[fid].source for fid in ids if facts[fid].source in tables),None)
        units=[u for u in package.analysis.get('archetypes',{}).get('units',[]) if u['fact_ids']==ids]
        purpose=group.get('purpose') or (units[0]['purpose'] if len(units)==1 else 'content')
        visualization=tables[tid].visualization if tid else None
        chart=visualization not in (None,'auto','table','metrics')
        outline.append(SlidePlan(title=group['title'],fact_ids=ids,table_id=tid,purpose=purpose,
            layout='chart' if chart else 'table' if tid else 'process' if purpose in ('process','timeline') else 'columns',
            chart_type=visualization if chart else 'auto'))
    if package.analysis.get('editorial'):
        from .composer import compose_slide
        from .uploads import assign_images
        from .audit import audit_scenes,repair_scenes
        # Probe the same final scene that generation will export. Raw compose()
        # can still contain a provisional table before native chart conversion.
        variant=VariantPlan(key='executive',title='Readability preview',slides=outline)
        image_groups=assign_images(package,variant)
        bad=[];fit_issues=[]
        for index,slide in enumerate(outline):
            try:
                scene=compose_slide(variant,package,index,image_groups);repair_scenes([scene],package)
                defects=[f for f in audit_scenes([scene],package) if f.code in
                    ('container_overflow','out_of_bounds','text_overflow','table_overflow','chart_overflow','overlap','readability')]
                if defects:
                    bad.append(index+1)
                    fit_issues.append({'slide':index+1,'pattern_id':scene.pattern_id,'findings':[f.model_dump() for f in defects],'fields':[{
                        'text':e.text,'fact_ids':e.source_ids,'current_font_size':e.size,
                        'minimum_font_size':18 if e.role=='title' else 16,
                        'width':round(e.box.w),'height':round(e.box.h),
                        'target_max_characters':max(12,int(e.box.w/9)*max(1,int(e.box.h/22)))}
                        for e in scene.elements if e.kind=='text' and (e.source_ids or e.role=='title')]})
            except ValueError as exc:
                bad.append(index+1);fit_issues.append({'slide':index+1,'message':str(exc),'fields':[]})
        if bad:
            package.analysis['slide_budget']={'status':'needs_input','planned':None,'fit_issues':fit_issues,
                'message':'Не помещается читаемый текст на слайдах '+', '.join(map(str,bad))+'. Требуется повторное сокращение без изменения числа слайдов.'}
            return
    else:
        outline=fit_storyboard(package,outline)
    package.analysis['storyboard']=[s.model_dump() for s in outline]
    package.analysis['slide_budget']={'status':'adjusted','requested':package.constraints.slides,
        'count_mode':package.constraints.count_mode,'planned':len(outline),'required':len(outline),
        'requested_range':package.analysis['narrative']['requested_range'],
        'message':f'По смыслу и читаемости предложено {len(outline)} слайдов. Можно принять или пересобрать план.'}
