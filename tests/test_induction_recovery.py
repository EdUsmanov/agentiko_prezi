import asyncio
import json
from types import SimpleNamespace
import pytest
from studio.analysis import analyze_meaning
from studio.content import parse_content
from studio.document import structure_document


def meaning(pid):
    return {'pattern_id': pid, 'roles': ['evidence'], 'density': 'medium',
            'purpose': 'content', 'reusable': True}


def test_image_free_layouts_use_bounded_text_batches():
    inventory={'width':960,'height':540,'patterns':[{'id':f'p{i}','source_slide':1 if i<4 else 0} for i in range(28)]}
    class Gateway:
        settings=SimpleNamespace(mode='api')
        calls=[]
        async def json_request(self,stage,payload,**kwargs):
            self.calls.append((len(payload['patterns']),len(kwargs.get('images',[]))))
            return {'patterns':[meaning(p['id']) for p in payload['patterns']]}
    gateway=Gateway();report=asyncio.run(analyze_meaning(inventory,gateway,images={1:b'image'}))
    assert report['status']=='completed'
    assert gateway.calls==[(4,1)]+[(4,0)]*6
    assert [p['pattern_id'] for p in report['patterns']]==[f'p{i}' for i in range(28)]


def test_template_split_and_resume_only_failed_blocks(tmp_path):
    inventory = {'patterns': [{'id': f'p{i}', 'source_slide': i} for i in range(5)],
                 'width': 960, 'height': 540}
    class Gateway:
        settings = SimpleNamespace(mode='api', data_dir=tmp_path, model_id='test')
        def __init__(self, fail): self.requests=[]; self.calls=[]; self.fail=fail
        async def json_request(self, stage, payload, **kw):
            ids = [p['id'] for p in payload['patterns']]
            self.requests.append(ids)
            if self.fail and 'p2' in ids: raise TimeoutError('SECRET provider body')
            return {'patterns': [meaning(pid) for pid in ids]}
    first = Gateway(True)
    result = asyncio.run(analyze_meaning(inventory, first))
    assert result['status']=='partial' and result['excluded_pattern_ids']==['p2']
    assert {p['pattern_id'] for p in result['patterns']}=={'p0','p1','p3','p4'}
    assert 'SECRET' not in json.dumps(first.induction_errors)
    # The split decision is remembered; only the failed single pattern is retried.
    second = Gateway(True)
    asyncio.run(analyze_meaning(inventory, second))
    assert second.requests==[['p2'],['p2']]
    recovered = asyncio.run(analyze_meaning(inventory, Gateway(False)))
    assert recovered['status']=='completed' and len(recovered['patterns'])==5


def test_document_failed_batch_keeps_all_facts_and_reuses_successes(prepared,tmp_path):
    _,_,package=prepared
    source='\n'.join(f'Значение метрики {i} составляет {i+1}.' for i in range(50))
    class Gateway:
        settings=SimpleNamespace(mode='api',data_dir=tmp_path/'cache',model_id='test')
        def __init__(self): self.requests=[]; self.calls=[]
        async def json_request(self,stage,payload,**kw):
            ids=[f['id'] for f in payload['blocks']];self.requests.append(ids)
            assert len(ids)<=24
            if 'f25' in ids: raise TimeoutError()
            return {'blocks':[{'fact_id':fid,'kind':'body'} for fid in ids]}
    package.content=parse_content(source)
    original=[f.model_dump() for f in package.content.facts]
    first=Gateway()
    asyncio.run(structure_document(package,first))
    assert package.analysis['document_structure']['status']=='degraded'
    assert [f.model_dump() for f in package.content.facts]==original
    assert len(first.requests)==4
    package.content=parse_content(source)
    second=Gateway()
    asyncio.run(structure_document(package,second))
    assert len(second.requests)==2
    assert all('f25' in ids for ids in second.requests)


@pytest.mark.parametrize('kind',['heading','visualization'])
def test_unsafe_roles_cannot_hide_numeric_table_or_list_evidence(prepared,kind):
    _,_,p=prepared
    p.content=parse_content('Выручка 100\n- Существенный факт\n| X | Y |\n|---|---|\n| A | 1 |')
    original=[f.text for f in p.content.facts]
    class Gateway:
        settings=SimpleNamespace(mode='api')
        async def json_request(self,stage,payload,**kw):
            return {'blocks':[{'fact_id':f['id'],'kind':kind} for f in payload['blocks']]}
    asyncio.run(structure_document(p,Gateway()))
    assert [f.text for f in p.content.facts]==original
    assert p.analysis['document_structure']['status']=='degraded'


def test_all_heading_response_cannot_empty_document(prepared):
    _,_,p=prepared
    p.content=parse_content('Первый тезис\nВторой тезис')
    original=[f.text for f in p.content.facts]
    class Gateway:
        settings=SimpleNamespace(mode='api')
        async def json_request(self,stage,payload,**kw):
            return {'blocks':[{'fact_id':f['id'],'kind':'heading'} for f in payload['blocks']]}
    asyncio.run(structure_document(p,Gateway()))
    assert [f.text for f in p.content.facts]==original


def test_failure_diagnostics_persist_without_provider_body(prepared,monkeypatch):
    import studio.pipeline as pipeline
    from dataclasses import replace
    settings,store,p=prepared
    class Gateway:
        calls=[{'stage':'template_analyst','status':'failed','error_type':'TimeoutError'}]
        induction_errors=[{'stage':'template_analyst','error_type':'TimeoutError','attempt':2}]
        def __init__(self,*args): pass
    async def fail(*args,**kwargs): raise ValueError('Не удалось проверить макеты')
    monkeypatch.setattr(pipeline,'ModelGateway',Gateway)
    monkeypatch.setattr(pipeline,'prepare_intelligence',fail)
    pipeline.prepare(store,p.id,'Факт один.\nФакт два.','','',2,replace(settings,mode='api'))
    job=store.get(p.id)
    assert job['state']=='failed' and 'ValueError' in job['error']
    assert 'TimeoutError' not in job['error']  # old recovered errors are not this failure's cause
    report=json.loads((store.directory(p.id)/'failure-report.json').read_text())
    assert report['model_calls'][0]['stage']=='template_analyst'
    assert report['phase']


def test_partial_template_excludes_unverified_geometry(prepared,monkeypatch):
    import studio.analysis as analysis
    _,store,p=prepared
    usable=next(x for x in p.template.patterns if x.title_zone and x.body_zones)
    async def partial(*args,**kw):
        return {'status':'partial','method':'text_and_geometry','patterns':[meaning(usable.id)],
                'excluded_pattern_ids':[x.id for x in p.template.patterns if x.id!=usable.id]}
    async def stop(package,*args,**kw):
        assert usable.id in [x.id for x in package.template.patterns]
        assert all(x.source_slide==usable.source_slide for x in package.template.patterns)
        assert package.analysis['warnings']
        raise RuntimeError('test reached document')
    from studio import template_analysis
    monkeypatch.setattr(template_analysis,'analyze_meaning',partial)
    import studio.document
    monkeypatch.setattr(studio.document,'structure_document',stop)
    gateway=SimpleNamespace(settings=SimpleNamespace(mode='api',model_id='test'))
    with pytest.raises(RuntimeError,match='test reached document'):
        asyncio.run(analysis.prepare_intelligence(p,store.directory(p.id)/'input.pptx',gateway,lambda *_:None))


def test_document_failure_still_reaches_validated_planning(prepared):
    from studio.analysis import prepare_intelligence
    from studio.planner import extractive_plans, validate_plans
    _,store,p=prepared
    original=[f.text for f in p.content.facts]
    class Gateway:
        settings=SimpleNamespace(mode='api',model_id='test')
        async def json_request(self,stage,payload,**kw):
            if stage=='template_analyst':
                return {'patterns':[meaning(pattern['id']) for pattern in payload['patterns']]}
            if stage=='document': raise TimeoutError()
            if stage=='sections':
                return {'chapters':[{'title':'Контекст','sections':list(range(6))},
                                    {'title':'Результаты','sections':list(range(6,12))}]}
            return extractive_plans(p).model_dump()
    result=asyncio.run(prepare_intelligence(p,store.directory(p.id)/'input.pptx',Gateway(),lambda *_:None))
    assert result.analysis['document_structure']['status']=='degraded'
    assert [f.text for f in result.content.facts]==original
    assert validate_plans(result.prepared_plans,result)


def test_checkpoint_invalidates_input_model_and_pipeline(tmp_path,monkeypatch):
    from studio.induction import validated_request
    import studio.cache_version as library
    from studio.document import DocumentRoles
    monkeypatch.setattr(library,'stage_version',lambda stage:'v1')
    class Gateway:
        def __init__(self,model):
            self.settings=SimpleNamespace(data_dir=tmp_path,model_id=model)
            self.calls=[];self.requests=0
        async def json_request(self,stage,payload,**kw):
            self.requests+=1
            return {'blocks':[{'fact_id':'f1','kind':'body'}]}
    def invoke(gateway,text='A'):
        return asyncio.run(validated_request(gateway,'document',{'text':text},DocumentRoles.model_json_schema(),
            lambda raw:DocumentRoles.model_validate(raw).model_dump(),timeout=1))
    first=Gateway('model1');invoke(first);invoke(first)
    assert first.requests==1
    invoke(first,'B');assert first.requests==2
    second=Gateway('model2');invoke(second);assert second.requests==1
    monkeypatch.setattr(library,'stage_version',lambda stage:'v2')
    fresh=Gateway('model1');invoke(fresh);assert fresh.requests==1
    for path in (tmp_path/'analysis-cache').glob('*.json'):
        assert path.stat().st_mode & 0o777 == 0o600


def test_cancelled_model_request_is_not_retried():
    from studio.induction import validated_request
    class Gateway:
        settings=SimpleNamespace(mode='api')
        calls=0
        async def json_request(self,*args,**kw):
            self.calls+=1
            raise asyncio.CancelledError()
    gateway=Gateway()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(validated_request(gateway,'document',{}, {},lambda raw:raw,timeout=1))
    assert gateway.calls==1


def test_table_fact_cannot_be_removed_as_visualization_directive(prepared):
    _,_,p=prepared
    p.content=parse_content('Исходный тезис.\n| Категория | Значение |\n|---|---|\n| A | 10 |')
    evidence=next(f for f in p.content.facts if f.source=='t1')
    evidence.text='Тип графика: таблица A 10'
    gateway=SimpleNamespace(settings=SimpleNamespace(mode='extractive'))
    asyncio.run(structure_document(p,gateway))
    assert any(f.id==evidence.id for f in p.content.facts)
    assert not p.content.directives
