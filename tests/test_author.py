import asyncio
from types import SimpleNamespace
from studio.author import expand_brief

class FakeGateway:
    settings=SimpleNamespace(mode='api')
    def __init__(self,proposals):self.proposals=proposals
    async def json_request(self,*args,**kwargs):return {'proposals':self.proposals}

def test_brief_drafts_are_labelled_and_do_not_mutate_prepared(prepared):
    _,_,package=prepared
    package.content.facts=package.content.facts[:1]
    package.constraints.slides=2
    updated,warning=asyncio.run(expand_brief(package,FakeGateway(['Предлагается начать с пилотного процесса и согласовать критерии оценки.']),10))
    assert warning is None
    assert len(package.content.facts)==1
    assert len(updated.content.facts)==2
    assert updated.content.facts[-1].source=='model_proposal'
    assert 'требует проверки' in updated.content.facts[-1].text

def test_reject_invented_numbers_in_drafts(prepared):
    _,_,package=prepared
    package.content.facts=package.content.facts[:1]
    package.constraints.slides=2
    updated,warning=asyncio.run(expand_brief(package,FakeGateway(['Ожидаемый эффект составит 99 процентов за месяц после внедрения.']),10))
    assert warning
    assert len(updated.content.facts)==1
