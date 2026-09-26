"""Re-test generation against existing immutable packages, without re-analysis."""
import argparse
import json
from pathlib import Path
import httpx
from smoke_demo import wait, ROOT


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('packages',nargs='+')
    args=parser.parse_args()
    report=[]
    target=ROOT/'test-results/deeppresenter-final-regeneration.json'
    target.parent.mkdir(exist_ok=True)
    with httpx.Client(base_url='http://127.0.0.1:8765',timeout=60,trust_env=False) as client:
        for pid in args.packages:
            source=client.get('/api/jobs/'+pid);source.raise_for_status()
            response=client.post('/api/generate',json={'package_id':pid});response.raise_for_status()
            job=wait(client,response.json()['id'])
            item={k:job.get(k) for k in ('id','state','elapsed_seconds','errors','warnings','engine','model_degraded','planning_source','contextual_audit','composition_diversity')}
            item.update(template=source.json()['template']['name'],package_id=pid)
            report.append(item)
            target.write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps(item,ensure_ascii=False),flush=True)
    if any(item['state']!='completed' for item in report):
        raise SystemExit('Some runs require review; inspect the report')


if __name__=='__main__':
    main()
