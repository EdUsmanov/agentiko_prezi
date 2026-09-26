from io import BytesIO
from pathlib import Path
import json
import shutil
import struct
import subprocess
from types import SimpleNamespace
import httpx
import pytest
from fontTools.ttLib import TTFont
from studio.config import ROOT
from studio import font_decoder, open_fonts, cache_version
from studio.embedded_fonts import extract_embedded_font, unpack_font, inspect_font
from studio.font_identity import requested_face, matches_face, binary_identity
from tests.test_fonts import embed, eot


def medium():
    return (ROOT/'fonts/Montserrat-Medium.ttf').read_bytes()


def test_real_node_decoder_roundtrip():
    if not shutil.which('node'):
        pytest.skip('Node.js is an optional MTX dependency')
    raw=(ROOT/'fonts/Play-Regular.ttf').read_bytes()
    assert font_decoder.decode_mtx(eot(raw))==raw


def test_mtx_expansion_is_bounded_before_allocation():
    node=shutil.which('node')
    if not node:
        pytest.skip('Node.js is an optional MTX dependency')
    module=(ROOT/'studio/_vendor/mtx_decompressor/index.mjs').as_uri()
    # LZ header claims 0xFFFFFF output bytes; its history window crosses 16 MiB.
    script=f'''import {{unpackMtx}} from {json.dumps(module)};
const data=Uint8Array.from([1,0,0,0,0,0,13,0,0,13,255,255,255]);
try {{unpackMtx(data,data.length);process.exitCode=2;}}
catch(e) {{if(e.message !== 'MTX allocation limit') throw e;}}
'''
    result=subprocess.run([node,'--max-old-space-size=128','--input-type=module','-e',script],
                          capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr


def test_regular_slot_uses_binary_medium_weight(template,tmp_path):
    from zipfile import ZipFile,ZIP_DEFLATED
    from defusedxml import ElementTree as ET
    from studio.embedded_fonts import P
    source=tmp_path/'medium.pptx'
    embed(template,source,medium())
    with ZipFile(source) as z:
        parts={n:z.read(n) for n in z.namelist()}
    root=ET.fromstring(parts['ppt/presentation.xml'])
    root.find('.//'+P+'embeddedFont/'+P+'font').set('typeface','Montserrat Medium')
    parts['ppt/presentation.xml']=ET.tostring(root)
    with ZipFile(source,'w',ZIP_DEFLATED) as z:
        for name,data in parts.items():z.writestr(name,data)
    path,origin,issues=extract_embedded_font(source,tmp_path,'Montserrat Medium')
    assert path and not issues and Path(path).read_bytes()==medium()
    assert origin['binary_identity']['weight']==500


def test_identity_checks_weight_even_with_matching_name():
    assert requested_face('Montserrat Medium')==('Montserrat',500,False)
    assert requested_face('Open Sans Semi Bold Italic')==('Open Sans',600,True)
    assert matches_face(medium(),'Montserrat Medium')
    assert not matches_face(medium(),'Mont Medium')
    font=TTFont(BytesIO(medium()));font['OS/2'].usWeightClass=400
    output=BytesIO();font.save(output);font.close()
    assert not matches_face(output.getvalue(),'Montserrat Medium')


def test_mtx_path_and_permissions(monkeypatch):
    raw=(ROOT/'fonts/Play-Regular.ttf').read_bytes()
    seen=[]
    monkeypatch.setattr(font_decoder,'decode_mtx',lambda data:(seen.append(data) or raw))
    assert unpack_font(eot(raw,4))==(raw,'EOT/MTX')
    assert len(seen)==1
    for offset,value in ((12,0x10000004),(32,2)):
        bad=bytearray(eot(raw,4));struct.pack_into('<I' if offset==12 else '<H',bad,offset,value)
        with pytest.raises(ValueError):unpack_font(bytes(bad))
    assert len(seen)==1


def test_decoder_limits_and_no_secret_environment(monkeypatch):
    monkeypatch.setenv('STUDIO_NODE_EXECUTABLE','/operator/node')
    monkeypatch.setenv('NODE_OPTIONS','--require=/evil.js')
    monkeypatch.setenv('LLM_API_KEY','test-secret')
    def run(args,**kw):
        assert args[0]=='/operator/node' and '--max-old-space-size=128' in args
        assert kw['timeout']==10
        assert 'NODE_OPTIONS' not in kw['env'] and 'LLM_API_KEY' not in kw['env']
        raise subprocess.TimeoutExpired(args,10)
    monkeypatch.setattr(font_decoder.subprocess,'run',run)
    with pytest.raises(ValueError,match='10 секунд'):font_decoder.decode_mtx(b'test')
    with pytest.raises(ValueError,match='16 МБ'):font_decoder.decode_mtx(b'x'*(font_decoder.MAX_FONT_BYTES+1))


@pytest.mark.parametrize('result',[SimpleNamespace(returncode=1,stdout=b'private'),
                                  SimpleNamespace(returncode=0,stdout=b'not a font')])
def test_decoder_bad_response(monkeypatch,result):
    monkeypatch.setenv('STUDIO_NODE_EXECUTABLE','/operator/node')
    monkeypatch.setattr(font_decoder.subprocess,'run',lambda *a,**k:result)
    with pytest.raises(ValueError,match='повреждён'):font_decoder.decode_mtx(b'test')


def css_server(monkeypatch,tmp_path,case='ok'):
    seen=[]
    raw=medium()
    def respond(request):
        seen.append(str(request.url))
        if request.url.path.endswith('METADATA.pb'):
            return httpx.Response(200,content=b'license: "OFL"\nfonts {\n name: "Montserrat"\n full_name: "Montserrat Regular"\n filename: "Montserrat[wght].ttf"\n}\n')
        if request.url.path.endswith('OFL.txt'):
            return httpx.Response(200,content=b'SIL OPEN FONT LICENSE test')
        if request.url.host=='fonts.googleapis.com':
            if case=='redirect':return httpx.Response(302,headers={'Location':'http://127.0.0.1/private'})
            url='https://evil.example/font.ttf' if case=='external' else 'https://fonts.gstatic.com/s/montserrat/medium.ttf'
            extra='unicode-range: U+0000-00FF;' if case=='subset' else ''
            return httpx.Response(200,text=f"@font-face {{ font-family: 'Montserrat'; font-style: normal; font-weight: 500; src: url({url}) format('truetype'); {extra} }}")
        assert request.url.host=='fonts.gstatic.com'
        if case=='oversize':return httpx.Response(200,headers={'Content-Length':str(17*1024*1024)},content=b'x')
        if case=='wrong_weight':
            font=TTFont(BytesIO(raw));font['OS/2'].usWeightClass=400
            output=BytesIO();font.save(output);font.close()
            return httpx.Response(200,content=output.getvalue())
        if case=='restricted':
            font=TTFont(BytesIO(raw));font['OS/2'].fsType=2
            output=BytesIO();font.save(output);font.close()
            return httpx.Response(200,content=output.getvalue())
        if case=='missing_names':
            font=TTFont(BytesIO(raw));del font['name']
            output=BytesIO();font.save(output);font.close()
            return httpx.Response(200,content=output.getvalue())
        return httpx.Response(200,content=raw)
    original=httpx.Client
    monkeypatch.setattr(open_fonts.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    monkeypatch.setattr(open_fonts,'CACHE',tmp_path/'cache')
    return seen


def test_css_fallback_and_offline_cache(monkeypatch,tmp_path):
    seen=css_server(monkeypatch,tmp_path)
    path,issue=open_fonts.download_face('Montserrat Medium')
    assert path and not issue and Path(path).read_bytes()==medium()
    meta=json.loads(Path(path).with_suffix('.json').read_text())
    assert meta['source'].startswith('https://fonts.gstatic.com/')
    assert meta['license']=='OFL-1.1'
    calls=len(seen)
    assert open_fonts.download_face('Montserrat Medium')==(path,'') and len(seen)==calls
    Path(path).write_bytes(b'corrupted')
    assert open_fonts.download_face('Montserrat Medium')==(path,'') and len(seen)>calls


@pytest.mark.parametrize('case',['redirect','external','subset','oversize','wrong_weight','restricted','missing_names'])
def test_css_unsafe_results_are_not_cached(monkeypatch,tmp_path,case):
    seen=css_server(monkeypatch,tmp_path,case)
    path,issue=open_fonts.download_face('Montserrat Medium')
    assert not path and issue and not list(tmp_path.rglob('*.ttf'))
    assert not any('evil.example' in url or '127.0.0.1' in url for url in seen)


@pytest.mark.parametrize('url',['https://fonts.gstatic.com.evil.test/s/a.ttf',
    'https://fonts.gstatic.com:443/s/a.ttf','https://user@fonts.gstatic.com/s/a.ttf',
    'http://fonts.gstatic.com/s/a.ttf'])
def test_font_host_allowlist(url):
    assert not open_fonts.allowed_url(url)


def test_decoder_change_invalidates_library(monkeypatch,tmp_path):
    (tmp_path/'studio').mkdir()
    for name in ('requirements.lock','pyproject.toml'):(tmp_path/name).write_text('test')
    module=tmp_path/'studio/font_decoder.mjs';module.write_text('version1')
    monkeypatch.setattr(cache_version,'ROOT',tmp_path)
    before=cache_version.pipeline_version();module.write_text('version2')
    assert before!=cache_version.pipeline_version()
