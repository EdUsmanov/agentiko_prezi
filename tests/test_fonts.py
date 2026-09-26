import asyncio
from io import BytesIO
from pathlib import Path
import shutil
import struct
import time
from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree import ElementTree as ET
import pytest
from fontTools.ttLib import TTFont
from studio import fonts
from studio.config import ROOT, Settings
from studio.embedded_fonts import unpack_font, inspect_font, check_glyphs, P, R
from studio.pipeline import prepare, load_package, generate
from studio.store import Store
from studio.security import InputRejected, digest


def eot(raw,flags=0):
    header=bytearray(96)
    struct.pack_into('<4I',header,0,len(header)+len(raw),len(raw),0x10000,flags)
    struct.pack_into('<H',header,34,0x504C)
    return bytes(header)+raw


def embed(template, target, raw, relationship_target='fonts/font1.fntdata', external=False):
    with ZipFile(template) as z:
        parts={n:z.read(n) for n in z.namelist()}
    root=ET.fromstring(parts['ppt/presentation.xml'])
    item=ET.SubElement(ET.SubElement(root,P+'embeddedFontLst'),P+'embeddedFont')
    ET.SubElement(item,P+'font',{'typeface':'Play'})
    ET.SubElement(item,P+'regular',{R+'id':'rIdFontTest'})
    parts['ppt/presentation.xml']=ET.tostring(root)
    rels=ET.fromstring(parts['ppt/_rels/presentation.xml.rels'])
    attrs={'Id':'rIdFontTest','Type':R[1:-1]+'/font','Target':relationship_target}
    if external: attrs['TargetMode']='External'
    ET.SubElement(rels,'{http://schemas.openxmlformats.org/package/2006/relationships}Relationship',attrs)
    parts['ppt/_rels/presentation.xml.rels']=ET.tostring(rels)
    ct=ET.fromstring(parts['[Content_Types].xml'])
    ET.SubElement(ct,'{http://schemas.openxmlformats.org/package/2006/content-types}Default',{'Extension':'fntdata','ContentType':'application/x-fontdata'})
    parts['[Content_Types].xml']=ET.tostring(ct)
    parts['ppt/fonts/font1.fntdata']=raw
    with ZipFile(target,'w',ZIP_DEFLATED) as z:
        for name,data in parts.items(): z.writestr(name,data)


def test_local_face_aliases():
    regular=fonts.resolve_font('Montserrat')
    medium=fonts.resolve_font('  Montserrat-Medium  ')
    bold=fonts.resolve_font('Montserrat Bold')
    assert regular and medium and bold
    assert len({regular,medium,bold})==3
    assert 'Medium' in medium
    assert not fonts.resolve_font('Nonexistent Company Font')


def test_new_font_detected_without_restart(tmp_path,monkeypatch):
    monkeypatch.setattr(fonts,'font_roots',lambda:[tmp_path])
    assert not fonts.resolve_font('Play')
    shutil.copyfile(ROOT/'fonts/Play-Regular.ttf',tmp_path/'any-filename.TTF')
    assert fonts.resolve_font('Play').endswith('any-filename.TTF')


def test_bold_never_substitutes_for_regular(tmp_path,monkeypatch):
    monkeypatch.setattr(fonts,'font_roots',lambda:[tmp_path])
    shutil.copyfile(ROOT/'fonts/Montserrat-Bold.ttf',tmp_path/'bold.ttf')
    assert fonts.resolve_font('Montserrat Bold')
    assert not fonts.resolve_font('Montserrat')


@pytest.mark.parametrize('wrapped',[False,True])
def test_embedded_font_priority_and_exports(tmp_path,template,content,wrapped):
    raw=(ROOT/'fonts/Play-Regular.ttf').read_bytes()
    source=tmp_path/'embedded.pptx'
    embed(template,source,eot(raw) if wrapped else raw)
    settings=Settings(data_dir=tmp_path/'data')
    store=Store(settings.data_dir)
    job=store.create('preparation')
    shutil.copyfile(source,store.directory(job['id'])/'input.pptx')
    prepare(store,job['id'],content,'','',5)
    package=load_package(store,job['id'])
    assert package.template.font_origin['kind']=='embedded'
    assert Path(package.template.font_file).is_relative_to(store.directory(job['id']))
    assert Path(package.template.font_file).read_bytes()==raw
    gen=store.create('generation',{'package_id':job['id'],'deadline_at':time.time()+300})
    asyncio.run(generate(store,gen['id'],settings))
    # Exports without model/visual approval require review, even when their
    # embedded fonts and deterministic checks are correct.
    done=store.get(gen['id'])
    assert done['state']=='needs_review',done
    assert done['quality_report']['errors']==0
    with ZipFile(store.directory(gen['id'])/'executive/deck.pptx') as z:
        assert z.read('ppt/fonts/font1.fntdata')==(eot(raw) if wrapped else raw)
    Path(package.template.font_file).write_bytes(raw+b'changed')
    with pytest.raises(ValueError,match='Шрифт изменён'): load_package(store,job['id'])


@pytest.mark.parametrize('flags',[4,0x10000000,0x20])
def test_unsupported_eot_not_silently_decoded(flags):
    with pytest.raises(ValueError): unpack_font(eot((ROOT/'fonts/Play-Regular.ttf').read_bytes(),flags))


def test_embedding_permissions_and_missing_glyphs():
    font=TTFont(ROOT/'fonts/Play-Regular.ttf')
    font['OS/2'].fsType=2
    out=BytesIO();font.save(out);font.close()
    with pytest.raises(ValueError,match='ограничения'): inspect_font(out.getvalue())
    with pytest.raises(InputRejected,match='нет символов'):
        check_glyphs(str(ROOT/'fonts/Play-Regular.ttf'),'\U0001FAE0')


@pytest.mark.parametrize('target,external',[('../../outside.ttf',False),('https://evil.test/f.ttf',True)])
def test_no_external_or_traversal_font(tmp_path,template,target,external):
    from studio.embedded_fonts import extract_embedded_font
    source=tmp_path/'bad.pptx'
    embed(template,source,(ROOT/'fonts/Play-Regular.ttf').read_bytes(),target,external)
    path,origin,issues=extract_embedded_font(source,tmp_path,'Play')
    assert not path and issues
    assert not (tmp_path/'embedded-fonts').exists()
