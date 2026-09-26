import asyncio
from types import SimpleNamespace
from studio.content_review import review
from studio.models import Plans,VariantPlan,SlidePlan


def test_identical_content_reuses_review_but_maps_findings_to_each_variant():
    plans=Plans(variants=[VariantPlan(key=k,title=k,slides=[SlidePlan(title='Title',fact_ids=['f1'])])
        for k in ('executive','analytical','story')])
    slides=[{'variant':v.key,'slide':1,'title':'Title','actual_text':'same','facts':[{'id':'f1','text':'Fact'}]}
        for v in plans.variants]
    class Gateway:
        calls=0
        async def json_request(self,stage,payload,**kwargs):
            self.calls+=1
            assert len(payload['slides'])==1
            return {'findings':[{'variant':'executive','slide':1,'title_quote':'Title','fact_ids':['f1'],
                'severity':'warning','code':'title_grounding','message':'Проверить заголовок'}]}
    gateway=Gateway();report=asyncio.run(review(slides,plans,gateway,'critic',180))
    assert report['status']=='completed',report
    assert gateway.calls==1 and report['checked']==3 and report['unique_slides']==1
    assert [f['variant'] for f in report['findings']]==['executive','analytical','story']


def test_changed_actual_text_is_never_deduplicated():
    plans=Plans(variants=[VariantPlan(key=k,title=k,slides=[SlidePlan(title='Title',fact_ids=[])])
        for k in ('executive','analytical','story')])
    slides=[{'variant':v.key,'slide':1,'title':'Title','actual_text':v.key,'facts':[]} for v in plans.variants]
    class Gateway:
        async def json_request(self,stage,payload,**kwargs):
            assert len(payload['slides'])==3
            return {'findings':[]}
    report=asyncio.run(review(slides,plans,Gateway(),'critic',180))
    assert report['unique_slides']==3 and report['status']=='completed'
