from dataclasses import replace
import time
import pytest
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

@pytest.mark.parametrize('extension',['pptx','potx','POTX'])
def test_api_upload_generate_and_xss(tmp_path,template,potx,content,extension):
    app=create_app(Settings(data_dir=tmp_path/'api'))
    content+='\nВидимый текст <img src=x onerror=alert(1)> без выполнения кода.'
    with TestClient(app) as client:
        source=template if extension=='pptx' else potx
        response=client.post('/api/prepare',data={'text':content,'slides':5},files={'template':('sample.'+extension,source.read_bytes(),'application/octet-stream')})
        assert response.status_code==202,response.text
        ready=wait_job(client,response.json()['id'])
        assert ready['state']=='ready',ready
        assert ready['template']['name']=='sample.'+extension
        assert (tmp_path/'api/jobs'/ready['id']/'input.pptx').read_bytes()==source.read_bytes()
        gen=client.post('/api/generate',json={'package_id':ready['id']})
        assert gen.status_code==202,gen.text
        done=wait_job(client,gen.json()['id'])
        assert done['state']=='needs_review',done
        assert done['quality_report']['errors']==0
        from io import BytesIO
        from zipfile import ZipFile
        from pptx import Presentation
        from studio.security import presentation_content_type,PPTX_MAIN
        output=client.get('/api/jobs/'+done['id']+'/files/executive/deck.pptx').content
        assert len(Presentation(BytesIO(output)).slides)==5
        with ZipFile(BytesIO(output)) as package:
            assert presentation_content_type(package)==PPTX_MAIN
        preview=client.get('/api/jobs/'+done['id']+'/files/executive/deck.html')
        assert preview.status_code==200
        assert 'sandbox' in preview.headers['content-security-policy']
        assert '&lt;img' in preview.text
        assert '<img src=x' not in preview.text
        assert client.get('/api/jobs/'+done['id']+'/files/input.pptx').status_code==404
        blocked=client.post('/api/generate',json={'package_id':ready['id']},headers={'Origin':'https://evil.example'})
        assert blocked.status_code==403
    assert app.state.store.get(done['id'])['state']=='needs_review'


def test_macro_template_extension_rejected(tmp_path,potx,content):
    with TestClient(create_app(Settings(data_dir=tmp_path/'macro'))) as client:
        response=client.post('/api/prepare',data={'text':content},files={'template':('sample.potm',potx.read_bytes())})
        assert response.status_code==422


def test_potx_revision(tmp_path,potx,content):
    with TestClient(create_app(Settings(data_dir=tmp_path/'revision'))) as client:
        response=client.post('/api/prepare',data={'text':content,'slides':5},files={'template':('sample.potx',potx.read_bytes())})
        original=wait_job(client,response.json()['id'])
        assert original['state']=='ready'
        revision=client.post('/api/packages/'+original['id']+'/revise',json={'instructions':'Сохранить исходные факты','slides':5})
        assert revision.status_code==202
        updated=wait_job(client,revision.json()['id'])
        assert updated['state']=='ready' and updated['template']['name']=='sample.potx'

def test_hard_deadline(tmp_path,template,content):
    app=create_app(Settings(data_dir=tmp_path/'timeout',deadline_seconds=.01))
    with TestClient(app) as client:
        prep=client.post('/api/prepare',data={'text':content,'slides':5},files={'template':('sample.pptx',template.read_bytes())}).json()
        assert wait_job(client,prep['id'])['state']=='ready'
        gen=client.post('/api/generate',json={'package_id':prep['id']}).json()
        assert wait_job(client,gen['id'])['state']=='timed_out'
        assert client.get('/api/jobs/'+gen['id']+'/files/presentations.zip').status_code==409
