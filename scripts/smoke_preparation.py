"""Live provider smoke test using synthetic input only. No user-uploaded files."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import shutil
import tempfile
import time
from studio.config import Settings, ROOT
from studio.pipeline import prepare, load_package, generate
from studio.store import Store

def main():
    from tests.conftest import template
    parent=ROOT/'test-results';parent.mkdir(exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='semantic-smoke-',dir=parent))
    source=template.__wrapped__(root)
    settings=replace(Settings.from_env(),data_dir=root/'data')
    if settings.mode!='api':
        raise ValueError('Configure API mode explicitly for a live test')
    class ProgressStore(Store):
        def update(self,jid,state=None,**fields):
            super().update(jid,state,**fields)
            if fields.get('phase'):print(fields['phase'],flush=True)
    store=ProgressStore(settings.data_dir)
    job=store.create('preparation',{'template_name':'Synthetic UI test'})
    shutil.copyfile(source,store.directory(job['id'])/'input.pptx')
    text='# Тестовый проект\n## Слайд 1. Контекст\nКоманда работает с заявками.\n## Слайд 2. Процесс\nЗаявки поступают через единый интерфейс.\n## Слайд 3. Доступ\nСтатус заявки доступен сотрудникам.'
    prepare(store,job['id'],text,'Тестовая команда','Сохранить исходные факты.',3,settings)
    package=load_package(store,job['id'])
    print(json.dumps({'preparation':package.analysis,'analysis_seconds':package.manifest['analysis_seconds']},ensure_ascii=False),flush=True)
    generation=store.create('generation',{'package_id':job['id'],'deadline_at':time.time()+300})
    asyncio.run(generate(store,generation['id'],settings))
    result=store.get(generation['id'])
    summary={key:result.get(key) for key in ['state','elapsed_seconds','planning_source','model_degraded','errors','composition_diversity']}
    summary['preparation_seconds']=package.manifest['analysis_seconds']
    summary['root']=str(root)
    (root/'report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    if package.analysis['template_semantics']['status']!='completed' or result['model_degraded']:
        raise RuntimeError('Model path degraded; inspect local manifest')

if __name__=='__main__':main()
