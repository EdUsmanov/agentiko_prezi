import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from studio.archetypes import validate_units, analyze_content_archetypes
from studio.archetype_catalog import Archetype, CATALOG, catalog_payload
from studio.models import Fact, SlidePlan, TableData
from studio.contracts import compatible, apply_meanings
from studio.storyboard import prepare_storyboard
from studio.planner import extractive_plans, validate_plans


def unit(ids, purpose, *slots):
    return {'fact_ids': ids, 'purpose': purpose, 'confidence': 'high',
            'slots': [{'role': role, 'fact_id': fid, 'quote': quote} for role, fid, quote in slots]}


@pytest.fixture
def process_facts():
    return [Fact(id='f1', text='Сначала принять заявку.', section='Процесс'),
            Fact(id='f2', text='Затем проверить заявку.', section='Процесс')]


def test_catalog_covers_schema_and_defines_speaker():
    from typing import get_args
    assert set(CATALOG) == set(get_args(Archetype))
    assert CATALOG['speaker'][2] == {'person': 1}
    assert len(catalog_payload()['archetypes']) == len(CATALOG)


def test_grounded_process_preserves_order(process_facts):
    raw = {'units': [unit(['f1','f2'], 'process', ('step','f1','принять заявку'), ('step','f2','проверить заявку'))]}
    assert validate_units(raw, process_facts)['units'][0]['purpose'] == 'process'
    bad = deepcopy(raw)
    bad['units'][0]['slots'].reverse()
    with pytest.raises(ValueError, match='order'):
        validate_units(bad, process_facts)


@pytest.mark.parametrize('change', ['missing_fact', 'reorder', 'invent_quote', 'one_step', 'structural', 'foreign_fact'])
def test_rejects_unsupported_semantic_claims(process_facts, change):
    item = unit(['f1','f2'], 'process', ('step','f1','принять заявку'), ('step','f2','проверить заявку'))
    if change == 'missing_fact': item['fact_ids'].pop()
    if change == 'reorder': item['fact_ids'].reverse()
    if change == 'invent_quote': item['slots'][0]['quote'] = 'Удалить все данные'
    if change == 'one_step': item['slots'].pop()
    if change == 'structural': item['purpose'] = 'cover'
    if change == 'foreign_fact': item['slots'][0]['fact_id'] = 'f999'
    with pytest.raises(ValueError):
        validate_units({'units': [item]}, process_facts)


def test_comparison_needs_entities_and_shared_criterion():
    facts = [Fact(id='f1', text='Цена: вариант А — 10, вариант Б — 20.')]
    item = unit(['f1'], 'comparison', ('entity','f1','вариант А'), ('entity','f1','вариант Б'), ('criterion','f1','Цена'))
    assert validate_units({'units':[item]}, facts)['units'][0]['purpose'] == 'comparison'
    item['slots'].pop()
    with pytest.raises(ValueError, match='slots'):
        validate_units({'units':[item]}, facts)


def test_comparison_can_ground_criterion_in_its_table_header():
    from studio.content import parse_content
    content = parse_content('# Тарифы\n| Вариант | Цена |\n|---|---|\n| А | 10 |\n| Б | 20 |')
    item = unit(['f1'], 'comparison', ('entity','f1','А'), ('entity','f1','Б'), ('criterion','f1','Цена'))
    assert validate_units({'units':[item]}, content.facts, content.tables)['units'][0]['purpose'] == 'comparison'
    unrelated = TableData(id='other',headers=['Цена'],rows=[['10']])
    with pytest.raises(ValueError, match='quote'):
        validate_units({'units':[item]}, content.facts, [unrelated])


def test_identical_facts_for_different_entities_are_not_deleted():
    from studio.content import parse_content
    content = parse_content('# Сравнение\n## Продукт А\nЦена 100 рублей.\n## Продукт Б\nЦена 100 рублей.')
    assert len(content.facts) == 2
    assert [f.section for f in content.facts] == ['Продукт А', 'Продукт Б']
    assert content.facts[0].id != content.facts[1].id


def test_speaker_without_person_is_not_invented(process_facts):
    with pytest.raises(ValueError, match='slots'):
        validate_units({'units':[unit(['f1','f2'],'speaker')]}, process_facts)


def test_speaker_name_can_come_from_its_heading():
    facts = [Fact(id='f1',text='Руководитель команды разработки.',section='Анна Смирнова')]
    item = unit(['f1'],'speaker',('person','f1','Анна Смирнова'))
    item['slots'][0]['source'] = 'section'
    assert validate_units({'units':[item]}, facts)['units'][0]['purpose']=='speaker'
    item['purpose']='process'
    item['slots'][0]['role']='step'
    with pytest.raises(ValueError):
        validate_units({'units':[item]}, facts)


def test_comparison_crosses_product_headings_not_real_chapters(prepared):
    from studio.content import parse_content
    _, _, p = prepared
    p.content = parse_content('# Сравнение\n## Продукт А\nЦена 100 рублей.\n## Продукт Б\nЦена 100 рублей.')
    p.analysis = {}
    item = unit(['f1','f2'],'comparison',('entity','f1','Продукт А'),('entity','f2','Продукт Б'),('criterion','f1','Цена'))
    item['slots'][0]['source'] = item['slots'][1]['source'] = 'section'
    class Gateway:
        settings=SimpleNamespace(mode='api')
        async def json_request(self, stage, payload, **kwargs):
            assert len(payload['facts'])==2
            return {'units':[item]}
    report=asyncio.run(analyze_content_archetypes(p,Gateway()))
    assert report['status']=='completed'
    prepare_storyboard(p)
    assert len(p.analysis['storyboard'])==1
    assert p.analysis['storyboard'][0]['purpose']=='comparison'


def test_atomic_units_not_split_or_forced_back_to_content(prepared, process_facts):
    _, _, package = prepared
    package.content.facts = process_facts
    package.content.tables = []
    package.constraints.slides = 2
    package.analysis = {'archetypes': {'units': [unit(['f1','f2'], 'process')]}}
    prepare_storyboard(package)
    assert package.analysis['slide_budget']['planned'] == 1
    assert package.constraints.slides == 2  # The user's original choice is not overwritten.
    plans = validate_plans(extractive_plans(package), package)
    for variant in plans.variants:
        assert len(variant.slides) == 1
        assert variant.slides[0].purpose == 'process'
        assert variant.slides[0].fact_ids == ['f1','f2']
        assert variant.slides[0].layout == 'process'
    pattern = next(p for p in package.template.patterns if p.title_zone and p.body_zones).model_copy(deep=True)
    pattern.purpose = 'process'; pattern.role = 'process'; pattern.id = 'process-layout'
    package.template.patterns.append(pattern)
    for variant in plans.variants:
        variant.slides[0].pattern_id = pattern.id
    assert validate_plans(plans, package) is plans


def test_special_units_not_merged_into_unrelated_slide(prepared):
    _, _, p = prepared
    p.content.facts = [Fact(id='f1', text='Первая проблема.', section='Обзор'),
                       Fact(id='f2', text='Рекомендуем действие.', section='Обзор')]
    p.content.tables = []; p.constraints.slides = 1
    p.analysis = {'archetypes': {'units': [unit(['f1'], 'problem'), unit(['f2'], 'recommendations')]}}
    prepare_storyboard(p)
    assert p.analysis['slide_budget']['planned'] == 2
    assert [s['purpose'] for s in p.analysis['storyboard']] == ['problem','recommendations']
    validate_plans(extractive_plans(p), p)


@pytest.mark.parametrize('purpose', ['speaker','comparison','process','summary','metrics'])
def test_specialized_layout_compatibility_and_generic_fallback(prepared, purpose):
    _, _, p = prepared
    pattern = p.template.patterns[0].model_copy(deep=True)
    pattern.role = 'statement'; pattern.purpose = purpose
    slide = SlidePlan(title='Тема', fact_ids=['f1'], purpose=purpose)
    assert compatible(pattern, slide)
    assert not compatible(pattern, slide.model_copy(update={'purpose': 'content'}))
    pattern.purpose = 'content'
    assert compatible(pattern, slide)  # Semantics survive absence of special layout.
    assert slide.purpose == purpose


def test_meaning_overrides_false_geometry_cover(prepared):
    _, _, p = prepared
    pattern = p.template.patterns[0]
    pattern.role = 'cover'
    apply_meanings(p.template, {'patterns': [{'pattern_id':pattern.id,'purpose':'speaker'}]})
    assert pattern.role != 'cover'
    assert compatible(pattern, SlidePlan(title='Спикер',fact_ids=['f1'],purpose='speaker'))


def test_failed_classification_keeps_all_facts(prepared, process_facts):
    _, _, p = prepared
    p.content.facts = process_facts
    p.analysis = {}
    class Gateway:
        settings = SimpleNamespace(mode='api')
        async def json_request(self, *args, **kwargs):
            raise TimeoutError()
    before = [f.model_dump() for f in p.content.facts]
    report = asyncio.run(analyze_content_archetypes(p, Gateway()))
    assert report['status'] == 'degraded'
    assert [fid for u in report['units'] for fid in u['fact_ids']] == ['f1','f2']
    assert all(u['purpose'] == 'content' for u in report['units'])
    assert [f.model_dump() for f in p.content.facts] == before


def test_model_induction_receives_catalog_and_grounded_inputs(prepared, process_facts):
    _, _, p = prepared
    p.content.facts = process_facts; p.analysis = {}
    class Gateway:
        settings = SimpleNamespace(mode='api')
        async def json_request(self, stage, payload, **kwargs):
            assert stage == 'content_archetypes'
            assert payload['catalog']['version'] == 1
            return {'units': [unit(['f1','f2'], 'process', ('step','f1','принять заявку'), ('step','f2','проверить заявку'))]}
    report = asyncio.run(analyze_content_archetypes(p, Gateway()))
    assert report['status'] == 'completed'
    prepare_storyboard(p)
    assert p.analysis['storyboard'][0]['purpose'] == 'process'


def test_composition_and_diversity_preserve_semantic_type(prepared, process_facts):
    from studio.composer import compose_variant
    from studio.diversity import ensure_diversity
    from studio.audit import audit_scenes
    _, _, p = prepared
    p.content.facts = process_facts
    p.content.tables = []
    p.constraints.slides = 1
    p.analysis = {'archetypes': {'units': [unit(['f1','f2'], 'process')]}}
    prepare_storyboard(p)
    plans = validate_plans(extractive_plans(p), p)
    decks = {v.key: compose_variant(v, p) for v in plans.variants}
    ensure_diversity(decks, p)
    for scenes in decks.values():
        assert all(s.purpose == 'process' for s in scenes)
        assert not any(f.code == 'archetype_changed' for f in audit_scenes(scenes, p))
    scenes = next(iter(decks.values()))
    scenes[0].purpose = 'comparison'
    assert any(f.code == 'archetype_changed' for f in audit_scenes(scenes, p))
