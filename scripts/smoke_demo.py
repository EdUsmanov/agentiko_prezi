"""Exercise the real local HTTP service on every indexed reference template."""
import json
import argparse
from pathlib import Path
import time
import httpx

ROOT=Path(__file__).resolve().parent.parent

def wait(client,jid,limit=330):
    start=time.monotonic()
    while time.monotonic()-start<limit:
        r=client.get('/api/jobs/'+jid);r.raise_for_status();job=r.json()
        if job['state'] not in ('accepted','running'):
            return job
        time.sleep(.3)
    raise TimeoutError(jid)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--slides',type=int,default=10)
    parser.add_argument('--limit',type=int,default=0)
    args=parser.parse_args()
    report=[]
    with httpx.Client(base_url='http://127.0.0.1:8765',timeout=60,trust_env=False) as client:
        refs=client.get('/api/references').json()
        if args.limit:
            refs=refs[:args.limit]
        for ref in refs:
            response=client.post('/api/prepare',data={'reference_id':ref['id'],'text':(ROOT/'web/demo.md').read_text(),'slides':args.slides,'audience':'Руководители проекта'})
            response.raise_for_status()
            prep=wait(client,response.json()['id'])
            if prep['state']!='ready':
                raise RuntimeError(prep)
            r=client.post('/api/generate',json={'package_id':prep['id']});r.raise_for_status()
            job=wait(client,r.json()['id'])
            item={'template':ref['name'],'preparation_id':prep['id'],'run_id':job['id'],'state':job['state'],
                'seconds':job.get('elapsed_seconds'),'errors':job.get('errors'),'variants':job.get('variants',[])}
            report.append(item)
            print(json.dumps(item,ensure_ascii=False),flush=True)
            if job['state'] not in ('completed','needs_review'):
                raise RuntimeError(job)
    target=ROOT/'test-results';target.mkdir(exist_ok=True)
    name='three-templates.json' if args.slides==10 and not args.limit else f'slides-{args.slides}.json'
    (target/name).write_text(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
