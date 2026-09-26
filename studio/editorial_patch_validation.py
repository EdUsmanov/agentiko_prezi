"""Keep a local text repair on its original subject and validate before caching."""
from copy import deepcopy
import re


def shortening_contracts(previous, allowed, feedback, budget):
    """Lock valid structure only when the requested change is text shortening."""
    validation = feedback.get('validation', '')
    messages = re.split(r'; (?=s\d+(?:b\d+|\b))', validation) if validation else []
    geometry = bool(feedback.get('geometry')) and not (feedback.get('issues') or feedback.get('essential_facts_to_restore'))
    contracts = []
    for index in allowed:
        local = [message for message in messages if re.match(rf's{index}(?:b\d+|\b)', message)]
        shorten = bool(local) and all('condense bullets and labels' in message for message in local)
        if not (shorten or geometry):
            continue
        slide = previous['slides'][index-1]
        labels = sum(len(b['group']) for b in slide['bullets'])
        contracts.append({'slide': index, 'purpose': slide['purpose'],
            'source_table_id': slide['source_table_id'], 'source_columns': slide['source_columns'],
            'chart_type': slide['chart_type'], 'relationship': slide['relationship'], 'rows': slide['rows'],
            'source_fact_ids': sorted({e['fact_id'] for b in slide['bullets'] for e in b['evidence']}),
            'groups': [b['group'] for b in slide['bullets']] if slide['purpose'] in ('timeline','process','hierarchy','structure') else None,
            'target_characters_per_bullet': max(1, (budget-labels)//len(slide['bullets']))})
    return contracts


def validate_contracts(changed, contracts):
    for contract in contracts:
        slide = changed['slides'][contract['slide']-1]
        prefix = f"s{contract['slide']}: shortening-only repair"
        for key in ('purpose','source_table_id','source_columns','chart_type','relationship','rows'):
            if slide[key] != contract[key]:
                raise ValueError(f'{prefix} must preserve {key}; shorten the existing text, do not replace its subject with another slide')
        refs = {e['fact_id'] for b in slide['bullets'] for e in b['evidence']}
        if not set(contract['source_fact_ids']) <= refs:
            raise ValueError(f"{prefix} must retain evidence for source facts {contract['source_fact_ids']}")
        if contract['groups'] is not None and [b['group'] for b in slide['bullets']] != contract['groups']:
            raise ValueError(f'{prefix} must preserve the ordered event/group labels')


def constrain_patch_schema(schema, contracts, allowed):
    """Expose per-slide constraints to JSON-mode models, including scope IDs."""
    schema = deepcopy(schema)
    by_index = {row['slide']: row for row in contracts}
    choices = []
    for index in allowed:
        replacement = deepcopy(schema['$defs']['Replacement'])
        replacement['properties']['slide'] = {'type': 'integer', 'const': index}
        if index in by_index:
            content = deepcopy(schema['$defs']['EditorialSlide'])
            for key in ('purpose','source_table_id','source_columns','chart_type','relationship','rows'):
                content['properties'][key] = {**content['properties'][key], 'const': by_index[index][key]}
            replacement['properties']['content'] = content
        choices.append(replacement)
    schema['properties']['replacements']['items'] = {'oneOf': choices}
    return schema


def validate_repaired_plan(changed, content, bounds, budget, require_cover, allowed):
    """Reject deterministic errors in changed slides without re-editing neighbours."""
    from .editorial import validate_plan
    from .editorial_repair import validation_targets
    try:
        validate_plan(changed, content, bounds, budget, require_cover=require_cover)
    except ValueError as error:
        messages = re.split(r'; (?=s\d+(?:b\d+|\b))', str(error))
        relevant = []
        for message in messages:
            targets = validation_targets(ValueError(message), len(changed['slides']))
            if not targets or set(targets) & set(allowed):
                relevant.append(message)
        if relevant:
            raise ValueError('; '.join(relevant)) from error


def numeric_evidence_hints(previous, allowed, content):
    """Locate numeric evidence for repairs without accepting or rewriting claims.

    Matching digits are candidates only: the model must verify attribution and
    units, and the independent semantic review still evaluates the final claim.
    """
    from .editorial import nums
    facts={fact.id:fact.text for fact in content.facts}
    fact_numbers={fid:set(nums(text)) for fid,text in facts.items()}
    hints=[]
    for index in allowed:
        slide=previous['slides'][index-1]
        for claim_index,claim in enumerate(slide['bullets'],1):
            cited=[e['fact_id'] for e in claim['evidence']]
            supported=set().union(*(fact_numbers.get(fid,set()) for fid in cited))
            missing=set(nums(claim['text']+' '+claim['group']))-supported
            if not missing:continue
            candidates=[{'fact_id':fid,'text':text,'matching_numbers':sorted(missing & fact_numbers[fid])}
                        for fid,text in facts.items() if missing & fact_numbers[fid]]
            hints.append({'claim_id':f's{index}b{claim_index}','text':claim['text'],
                'unsupported_numbers':sorted(missing),'current_fact_ids':cited,
                'candidate_count':len(candidates),
                'candidate_evidence':[{**row,'text':row['text'][:800]} for row in candidates[:12]],
                'action':'Add a fact_id only if it supports the SAME subject, quantity and unit. '
                         'Otherwise remove or reword the unsupported numeric claim using the original source wording. '
                         'Do not return unchanged text with unchanged evidence; matching digits alone are not proof.'})
    return hints
