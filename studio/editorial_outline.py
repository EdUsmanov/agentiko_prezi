"""Recovery for oversized editorial output: global outline, bounded slide batches."""
from pydantic import Field
from .models import StrictModel
from .editorial import EditorialSlide,EditorialPlan,Omission
from .archetype_catalog import Archetype
from .induction import validated_request


class OutlineSlide(StrictModel):
    title:str=Field(min_length=1,max_length=140)
    purpose:Archetype
    fact_ids:list[str]=Field(min_length=1,max_length=100)
    source_table_id:str|None=None
    source_columns:list[int]=Field(default_factory=list,max_length=8)
    chart_type:str='auto'


class Outline(StrictModel):
    slides:list[OutlineSlide]=Field(min_length=1,max_length=30)
    omitted:list[Omission]=Field(default_factory=list,max_length=300)


class SlideBatch(StrictModel):
    slides:list[EditorialSlide]=Field(min_length=1,max_length=2)


async def recover_plan(gateway,payload,bounds,progress=None):
    facts={f['id'] for f in payload['source']['facts']};tables={t['id'] for t in payload['source']['tables']}
    schema=Outline.model_json_schema();schema['properties']['slides'].update(minItems=bounds[0],maxItems=bounds[1])
    def check_outline(raw):
        result=Outline.model_validate(raw)
        if not bounds[0]<=len(result.slides)<=bounds[1]:raise ValueError('Outline count is outside requested range')
        if payload['include_cover'] and result.slides[0].purpose!='cover':raise ValueError('Reserve first slide for cover')
        for slide in result.slides:
            if not set(slide.fact_ids)<=facts or slide.source_table_id and slide.source_table_id not in tables:
                raise ValueError('Unknown source ID in outline')
            if slide.chart_type not in ('auto','table','line','column','bar','pie'):raise ValueError('Unknown chart type')
        required=set(payload.get('required_chart_types',[]))
        if not required<={s.chart_type for s in result.slides if s.source_table_id}:raise ValueError('Required chart missing')
        if any(o.fact_id not in facts for o in result.omitted):raise ValueError('Unknown omitted fact')
        return result.model_dump()
    if progress:progress('Большой ответ не завершён: сохраняем общий план и подготавливаем слайды небольшими группами')
    outline=await validated_request(gateway,'editorial_outline',payload,schema,check_outline,timeout=180,progress=progress)
    slides=[]
    for start in range(0,len(outline['slides']),2):
        rows=outline['slides'][start:start+2]
        batch_schema=SlideBatch.model_json_schema()
        batch_schema['properties']['slides'].update(minItems=len(rows),maxItems=len(rows))
        batch_schema['$defs']['EditorialSlide']['required'].append('purpose')
        batch_schema['$defs']['Citation']['properties'].pop('quote',None)
        def check_batch(raw):
            parsed=SlideBatch.model_validate(raw)
            if len(parsed.slides)!=len(rows):raise ValueError('Return exactly the requested slides')
            for actual,expected in zip(parsed.slides,rows):
                if actual.purpose!=expected['purpose'] or actual.source_table_id!=expected['source_table_id']:
                    raise ValueError('Preserve outline purpose and source table')
                if actual.source_columns!=expected['source_columns'] or actual.chart_type!=expected['chart_type']:
                    raise ValueError('Preserve data projection and chart type')
                if any(e.fact_id not in facts for b in actual.bullets for e in b.evidence):raise ValueError('Unknown source evidence')
            return parsed.model_dump()
        batch=await validated_request(gateway,'editorial_slides',
            {**payload,'outline':outline,'requested_slides':list(range(start+1,start+len(rows)+1)),
             'accepted_slides':slides},batch_schema,check_batch,timeout=180,progress=progress)
        slides.extend(batch['slides'])
    return EditorialPlan(slides=slides,omitted=outline['omitted']).model_dump()
