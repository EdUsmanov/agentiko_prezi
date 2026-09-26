"""Explicit live regression using an existing local package, never mutating it."""
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import shutil
import tempfile
import time
from studio.config import Settings,ROOT
from studio.models import PreparedPackage
from studio.pipeline import prepare,load_package,generate
from studio.store import Store


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('package_id')
    args=parser.parse_args()
    live=Settings.from_env()
    old_store=Store(live.data_dir)
    old_folder=old_store.directory(args.package_id)
    old=PreparedPackage.model_validate_json((old_folder/'package.json').read_text())
    parent=ROOT/'test-results';parent.mkdir(exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='semantic-v2-',dir=parent))
    settings=replace(live,data_dir=root/'data')
    class ProgressStore(Store):
        def update(self,jid,state=None,**fields):
            super().update(jid,state,**fields)
            if fields.get('phase'): print(fields['phase'],flush=True)
    store=ProgressStore(settings.data_dir)
    prep=store.create('preparation',{'template_name':old.template.name})
    folder=store.directory(prep['id'])
    shutil.copyfile(old_folder/'input.pptx',folder/'input.pptx')
    images=[]
    for image in old.images:
        target=folder/'input-images'/Path(image.path).name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(image.path,target)
        images.append(image.model_copy(update={'path':str(target)}).model_dump())
    (folder/'images.json').write_text(json.dumps(images,ensure_ascii=False))
    print('RESULT_ROOT='+str(root),flush=True)
    import studio.pipeline as pipeline
    original_intelligence=pipeline.prepare_intelligence
    async def diagnostic_intelligence(package,*args,**kwargs):
        try:
            return await original_intelligence(package,*args,**kwargs)
        finally:
            (root/'preparation-diagnostic.json').write_text(package.model_dump_json(indent=2))
            gateway=args[1]
            (root/'calls.json').write_text(json.dumps(gateway.calls,ensure_ascii=False,indent=2))
    pipeline.prepare_intelligence=diagnostic_intelligence
    prepare(store,prep['id'],'',old.constraints.audience,old.constraints.instructions,
        old.constraints.slides,settings,content_model=old.content,base_constraints=old.constraints)
    status=store.get(prep['id'])
    if status['state']!='ready':
        print(json.dumps({'state':status['state'],'error':status.get('error')},ensure_ascii=False),flush=True)
        raise SystemExit(1)
    package=load_package(store,prep['id'])
    generation=store.create('generation',{'package_id':prep['id'],'deadline_at':time.time()+300})
    try:
        asyncio.run(generate(store,generation['id'],settings))
    except Exception as exc:
        store.update(generation['id'],'failed',error=type(exc).__name__+': '+str(exc))
        raise
    result=store.get(generation['id'])
    summary={k:result.get(k) for k in ('state','errors','elapsed_seconds','analysis_seconds','warnings','visual_audit')}
    summary.update(preparation=prep['id'],generation=generation['id'],root=str(root))
    (root/'report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__': main()
