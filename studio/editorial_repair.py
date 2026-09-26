"""Bounded, slide-scoped editorial repairs; accepted neighbours stay immutable."""
from copy import deepcopy
import re
import json
from pydantic import Field
from .models import StrictModel
from .editorial import (EditorialSlide,EditorialPlan,EditorialReview,validate_plan,
    review_payload,validate_review,apply_plan)
from .content import SLIDE_RANGES
from .induction import validated_request, InductionFailure


class Replacement(StrictModel):
    slide: int = Field(ge=1,le=30)
    content: EditorialSlide


class EditorialPatch(StrictModel):
    replacements: list[Replacement] = Field(min_length=1,max_length=30)


def apply_replacements(previous,raw,allowed):
    patch=EditorialPatch.model_validate(raw)
    indices=[p.slide for p in patch.replacements]
    if len(indices)!=len(set(indices)) or set(indices)!=set(allowed):
        raise ValueError('Replace exactly the requested slide indices, once each')
    result=deepcopy(previous)
    for p in patch.replacements:result['slides'][p.slide-1]=p.content.model_dump()
    used={c['fact_id'] for s in result['slides'] for b in s['bullets'] for c in b['evidence']}
    result['omitted']=[o for o in result.get('omitted',[]) if o['fact_id'] not in used]
    return result


def plan_signature(plan,content=None):
    """Ignore resolved quotations/omission bookkeeping, not rendered content."""
    facts={f.id:f.text for f in content.facts} if content is not None else None
    slides=deepcopy(plan['slides'])
    for slide in slides:
        for bullet in slide['bullets']:
            for citation in bullet['evidence']:
                quote=citation.get('quote')
                if facts is None or quote is None or (citation['fact_id'] in facts and quote in facts[citation['fact_id']]):
                    citation.pop('quote',None)
    return json.dumps(slides,sort_keys=True,ensure_ascii=False)


def validation_targets(error,count):
    targets={int(i) for i in re.findall(r'\bs(\d+)(?:b\d+|\b)',str(error))}
    if hasattr(error,'errors'):
        for row in error.errors():
            loc=row.get('loc',())
            if len(loc)>1 and loc[0]=='slides' and isinstance(loc[1],int):targets.add(loc[1]+1)
    return sorted(i for i in targets if 1<=i<=count)


def review_targets(review,plan,content):
    targets={int(c['claim_id'].split('b')[0][1:]) for c in review['claims']
             if not c['supported'] or not c['meaning_preserved']}
    targets.update(i for i in review.get('repair_slide_indices',[]) if 1<=i<=len(plan['slides']))
    # If an older reviewer gives no location for an omitted essential fact,
    # attach it to the nearest existing source block, excluding the cover.
    positions={f.id:i for i,f in enumerate(content.facts)}
    for fid in review['missing_essential_fact_ids']:
        eligible=[]
        for i,s in enumerate(plan['slides'],1):
            if s['purpose']=='cover':continue
            refs=[positions[e['fact_id']] for b in s['bullets'] for e in b['evidence']]
            if refs:eligible.append((min(abs(positions[fid]-j) for j in refs),i))
        if eligible:targets.add(min(eligible)[1])
    return targets


def requested_chart_types(instructions):
    """Explicit output requirements from the user's constraints, never source commands."""
    types=[]
    if re.search(r'линейн\w*\s+(?:график|диаграмм)|\bline\s+chart',instructions,re.I):types.append('line')
    stacked=r'(?:накопительн\w*\s+столбчат\w*\s+(?:диаграмм\w*|график\w*)|столбчат\w*\s+(?:диаграмм\w*|график\w*)\s+с\s+накоплением|\bstacked\s+column\s+chart)'
    if re.search(stacked,instructions,re.I):types.append('column_stacked')
    plain=re.sub(stacked,'',instructions,flags=re.I)
    if re.search(r'столбчат\w*\s+(?:диаграмм|график)|\bcolumn\s+chart',plain,re.I):types.append('column')
    return types


async def prepare_with_targeted_repairs(package,gateway,progress=None,*,starting_plan=None,quality_feedback=None):
    original=package.original_content or package.content.model_copy(deep=True)
    package.original_content=original.model_copy(deep=True)
    from .editorial_tables import resolve_headers
    content,headers=await resolve_headers(original,gateway)
    package.analysis['table_header_repairs']=headers
    bounds=SLIDE_RANGES.get(package.constraints.size_preset,(package.constraints.slides,package.constraints.slides))
    if package.constraints.count_mode=='exact':bounds=(package.constraints.slides,package.constraints.slides)
    elif package.constraints.count_mode=='maximum':bounds=(1,package.constraints.slides)
    elif package.constraints.count_mode=='minimum':
        bounds=(package.constraints.slides,max(package.constraints.slides,SLIDE_RANGES.get(package.constraints.size_preset,(1,10))[1]))
    cover=getattr(package.constraints,'include_cover',True) and bounds[1]>1
    payload={'source':content.model_dump(),'slide_range':list(bounds),'include_cover':cover,
        'audience':package.constraints.audience,'instructions':package.constraints.instructions,
        'characters_per_slide':500,'templates':[{'id':p.id,'purpose':p.purpose,
        'source_slide':p.source_slide,'graphic_kind':p.graphic_kind,'fields':len(p.body_zones),
        'title_max_characters':max(16,int(p.title_zone.w/18)*max(1,int(p.title_zone.h/44))) if p.title_zone else 60,
        'field_capacity':[{'width':round(b.w),'height':round(b.h),
            'target_max_characters':max(12,int(b.w/9)*max(1,int(b.h/22)))} for b in p.body_zones]}
        for p in package.template.patterns if p.reusable]}
    required_charts=requested_chart_types(package.constraints.instructions) if content.tables else []
    payload['required_chart_types']=required_charts
    schema=EditorialPlan.model_json_schema()
    schema['properties']['slides'].update(minItems=bounds[0],maxItems=bounds[1])
    schema['$defs']['EditorialSlide']['required'].append('purpose')
    schema['$defs']['Citation']['properties'].pop('quote',None)
    def initial_plan(value):
        if any('purpose' not in slide for slide in value.get('slides',[])):
            raise ValueError('Every slide must explicitly provide purpose, including cover and timeline. Do not omit this field.')
        parsed=EditorialPlan.model_validate(value)
        present={s.chart_type for s in parsed.slides if s.source_table_id or s.rows}
        if not set(required_charts)<=present:
            raise ValueError('The user explicitly requires separate '+', '.join(required_charts)+' charts using the supplied data. Reserve slides for EACH required chart within slide_range; use source_table_id and source_columns, rows=[].')
        if not bounds[0]<=len(parsed.slides)<=bounds[1]:raise ValueError('Slide count must satisfy slide_range')
        return parsed.model_dump()
    if progress:progress('Отбираем главные мысли, включая титульный слайд в общий объём')
    # Syntax/schema recovery may retry the initial response. Content corrections
    # below never request a second whole-deck rewrite.
    if starting_plan is not None:
        raw=initial_plan(deepcopy(starting_plan))
    else:
        try:
            raw=await validated_request(gateway,'editorial',payload,schema,initial_plan,
                timeout=420,progress=progress,split_group=True)
        except InductionFailure as error:
            if error.fatal:raise
            from .editorial_outline import recover_plan
            raw=initial_plan(await recover_plan(gateway,payload,bounds,progress))
    known_issues=quality_feedback or []
    if any(not isinstance(issue.get('slide'),int) or not 1<=issue['slide']<=len(raw['slides']) for issue in known_issues):
        raise ValueError('Quality feedback must address an existing slide')
    history=[];semantic_corrections=[];validation_corrections=[];seen_plans=set()
    for attempt in range(9):
        seen_plans.add(plan_signature(raw,content))
        allowed=[];feedback={}
        if attempt==0 and known_issues:
            allowed=sorted({issue['slide'] for issue in known_issues})
            feedback={'quality_review':known_issues}
        else:
            try:
                raw=validate_plan(raw,content,bounds,500,require_cover=cover)
            except ValueError as exc:
                allowed=validation_targets(exc,len(raw['slides']))
                if not allowed:raise
                if str(exc) not in validation_corrections:validation_corrections.append(str(exc))
                feedback={'validation':str(exc),'previous_validation_corrections':validation_corrections[:]}
            else:
                candidate=deepcopy(package)
                apply_plan(candidate,raw,{},bounds,source_content=content)
                from .narrative import narrative_storyboard
                narrative_storyboard(candidate)
                budget=candidate.analysis.get('slide_budget',{})
                geometry_bad=budget.get('status')=='needs_input'
                # Deterministic fit failures are fixed before spending another
                # model request reviewing text which must change anyway.
                review={'claims':[],'missing_essential_fact_ids':[],'repair_slide_indices':[],
                        'narrative_coherent':False,'explanation':'Geometry requires repair before semantic review'}
                issues=[];semantic_bad=False
                if not geometry_bad:
                    inp=review_payload(raw,content)
                    if known_issues:inp['additional_checks']=known_issues
                    review=await validated_request(gateway,'editorial_review',inp,EditorialReview.model_json_schema(),
                        lambda value:validate_review(value,[c['claim_id'] for c in inp['claims']],
                            [f.id for f in content.facts]),timeout=420,progress=progress)
                    issues=[c for c in review['claims'] if not c['supported'] or not c['meaning_preserved']]
                    semantic_bad=bool(issues or review['missing_essential_fact_ids'] or not review['narrative_coherent'])
                    candidate.analysis['editorial']['review']=review
                    if semantic_bad:semantic_corrections.append(review)
                if not semantic_bad and not geometry_bad:
                    candidate.analysis['editorial'].update(attempts=attempt+1,repair_history=history,
                        repair_method='targeted_slide_replacement',include_cover=cover)
                    package.content=candidate.content;package.analysis=candidate.analysis
                    return True
                targets=review_targets(review,raw,content)
                targets.update(f['slide'] for f in budget.get('fit_issues',[]))
                allowed=sorted(targets)
                feedback={'issues':issues,'essential_facts_to_restore':review['missing_essential_fact_ids'],
                    'explanation':review['explanation'],'geometry':budget.get('message') if geometry_bad else None,
                    'fields':budget.get('fit_issues',[]),'previous_semantic_corrections':semantic_corrections}
        package.analysis['editorial_repair_diagnostics']={
            'round':attempt+1,'slides':allowed,'feedback':deepcopy(feedback),
            'last_plan':deepcopy(raw),'history':deepcopy(history)}
        if attempt==8 or not allowed:break
        if progress:progress('Адресно исправляем слайды '+', '.join(map(str,allowed))+'. Остальные сохраняем.')
        patch_schema=EditorialPatch.model_json_schema()
        patch_schema['$defs']['Replacement']['properties']['slide']['enum']=allowed
        patch_schema['$defs']['EditorialSlide']['required'].append('purpose')
        patch_schema['$defs']['Citation']['properties'].pop('quote',None)
        patch_schema['properties']['replacements'].update(minItems=len(allowed),maxItems=len(allowed))
        previous=deepcopy(raw)
        from .editorial_patch_validation import (shortening_contracts, validate_contracts,
            constrain_patch_schema, validate_repaired_plan)
        contracts=shortening_contracts(previous,allowed,feedback,payload['characters_per_slide'])
        patch_schema=constrain_patch_schema(patch_schema,contracts,allowed)
        context_plan=deepcopy(previous)
        for slide in context_plan['slides']:
            for bullet in slide['bullets']:
                bullet['evidence']=[{'fact_id':e['fact_id']} for e in bullet['evidence']]
        repair_payload={**payload,'allowed_slide_indices':allowed,'previous_plan':context_plan,
            'revision_feedback':feedback,'repair_contracts':contracts,'instruction':'Return replacements ONLY for the allowed slides. Keep total count and order. Preserve every previously corrected qualification. Use fact_id citations only. Do not edit neighbours.'}
        def validate_patch(value):
            changed=apply_replacements(previous,value,allowed)
            if plan_signature(changed,content) in seen_plans:
                raise ValueError('Repair repeats an already rejected plan. Change the affected content or evidence to resolve revision_feedback; returning the same slide cannot fix it.')
            validate_contracts(changed,contracts)
            validate_repaired_plan(changed,content,bounds,payload['characters_per_slide'],cover,allowed)
            return EditorialPatch.model_validate(value).model_dump()
        patch=await validated_request(gateway,'editorial_repair',repair_payload,patch_schema,
            validate_patch,timeout=420,progress=progress)
        raw=apply_replacements(previous,patch,allowed)
        unchanged=[i for i in range(1,len(raw['slides'])+1) if i not in allowed]
        assert all(raw['slides'][i-1]==previous['slides'][i-1] for i in unchanged)
        history.append({'round':attempt+1,'slides':allowed,'unchanged_slides':unchanged,'feedback':feedback})
        package.analysis['editorial_repair_history']=history
    raise ValueError('Не удалось подтвердить смысл и читаемость после адресных исправлений. Исходный текст и журнал сохранены.')
