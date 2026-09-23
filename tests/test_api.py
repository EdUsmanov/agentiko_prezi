from dataclasses import replace
import time
from fastapi.testclient import TestClient
from studio.app import create_app
from studio.config import Settings

def wait_job(client,jid):
    until=time.monotonic()+30
    while time.monotonic()<until:
        result=client.get('/api/jobs/'+jid).json()
        if result['state'] not in ('accepted','running'):
            return result
        time.sleep(.05)
    raise AssertionError('job did not finish')

def test_api_upload_generate_and_xss(tmp_path,template,content):
    app=create_app(Settings(data_dir=tmp_path/'api'))
    content+='\nВидимый текст <img src=x onerror=alert(1)> без выполнения кода.'
    with TestClient(app) as client:
        response=client.post('/api/prepare',data={'text':content,'slides':5},files={'template':('sample.pptx',template.read_bytes(),'application/octet-stream')})
        assert response.status_code==202,response.text
        ready=wait_job(client,response.json()['id'])
        assert ready['state']=='ready',ready
        gen=client.post('/api/generate',json={'package_id':ready['id']})
        assert gen.status_code==202,gen.text
        done=wait_job(client,gen.json()['id'])
        assert done['state']=='completed',done
        preview=client.get('/api/jobs/'+done['id']+'/files/executive/deck.html')
        assert preview.status_code==200
        assert 'sandbox' in preview.headers['content-security-policy']
        assert '&lt;img' in preview.text
        assert '<img src=x' not in preview.text
        assert client.get('/api/jobs/'+done['id']+'/files/input.pptx').status_code==404
        blocked=client.post('/api/generate',json={'package_id':ready['id']},headers={'Origin':'https://evil.example'})
        assert blocked.status_code==403

def test_hard_deadline(tmp_path,template,content):
    app=create_app(Settings(data_dir=tmp_path/'timeout',deadline_seconds=.01))
    with TestClient(app) as client:
        prep=client.post('/api/prepare',data={'text':content,'slides':5},files={'template':('sample.pptx',template.read_bytes())}).json()
        assert wait_job(client,prep['id'])['state']=='ready'
        gen=client.post('/api/generate',json={'package_id':prep['id']}).json()
        assert wait_job(client,gen['id'])['state']=='timed_out'
        assert client.get('/api/jobs/'+gen['id']+'/files/presentations.zip').status_code==409
