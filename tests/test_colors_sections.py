import json
from zipfile import ZipFile
from PIL import Image
from pptx import Presentation
from pptx.util import Inches,Pt
from pptx.dml.color import RGBColor
from studio.colors import extract_colors
from studio.models import Box,Pattern,Element,SlideScene
from studio.artwork import safe_body_zone
from studio.planner import extractive_plans,validate_plans,assign_compositions
from studio.sections import add_dividers
from studio.composer import compose_variant
from studio.content import parse_content
from studio.render import render_pptx,render_html
from studio.audit import audit_scenes,repair_scenes


def test_color_roles_table_evidence_and_potx(template,potx,tmp_path):
    roles,summary=extract_colors(potx,tmp_path/'report')
    assert '#154A67' in roles['text.other'] and '#FFFFFF' in roles['background']
    assert summary['slides']==3 and summary['visual_accuracy_verified'] is False
    prs=Presentation(template)
    table=prs.slides[0].shapes.add_table(2,2, Inches(1), Inches(4), Inches(5), Inches(1)).table
    for ri in range(2):
        for ci in range(2):
            table.cell(ri,ci).fill.solid()
            table.cell(ri,ci).fill.fore_color.rgb=RGBColor.from_string('123456' if ri==0 else 'AABBCC')
    path=tmp_path/'colors.pptx';prs.save(path)
    roles,summary=extract_colors(path,tmp_path/'report2')
    assert summary['table_styles']['1']['header']=={'color':'#123456','opacity':1}
    assert summary['table_styles']['1']['body']=={'color':'#AABBCC','opacity':1}


def test_transparent_table_native_and_html(prepared,tmp_path):
    _,store,p=prepared
    e=Element(kind='table',box=Box(x=70,y=150,w=600,h=100),rows=[['Name','Value'],['A','10']],
        font=p.template.font,size=16,color=p.template.foreground,fill=p.template.accent,fill_opacity=.4)
    scene=SlideScene(title='Table',background=p.template.background,elements=[e],source_ids=[],layout='table')
    target=tmp_path/'table.pptx'
    render_pptx([scene],p.template,store.directory(p.id)/'input.pptx',target,verify_text=False)
    with ZipFile(target) as z:
        from lxml import etree
        root=etree.fromstring(z.read('ppt/slides/slide1.xml'))
        ns={'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
        assert root.xpath('count(//a:tr[2]/a:tc/a:tcPr/a:noFill)',namespaces=ns)==2
        assert root.xpath('//a:tr[1]/a:tc/a:tcPr/a:solidFill//a:alpha/@val',namespaces=ns)==['40000','40000']
        assert root.xpath('count(//a:tr[2]/a:tc/a:tcPr/a:lnB/a:solidFill)',namespaces=ns)==2
    render_html([scene],p.template,tmp_path/'table.html')
    html=(tmp_path/'table.html').read_text()
    assert 'transparent' in html and ',0.4)' in html


def test_sections_keep_budget_facts_and_native_artwork(prepared):
    _,_,p=prepared
    p.content=parse_content('# Проект\n## Контекст\nПервый факт.\nВторой факт.\nТретий факт.\n## Результаты\nРезультат один.\nРезультат два.\nРезультат три.')
    p.constraints.slides=6
    p.template.patterns.append(Pattern(id='divider',role='divider',source_slide=1,source_layout='Section',
        title_zone=Box(x=80,y=180,w=650,h=100),text_zones=[],title_size=32))
    plans=add_dividers(assign_compositions(extractive_plans(p),p),p)
    validate_plans(plans,p)
    for variant in plans.variants:
        assert len(variant.slides)==6
        assert any(s.layout=='divider' for s in variant.slides)
        assert {f for s in variant.slides for f in s.fact_ids}=={f.id for f in p.content.facts}
        scenes=compose_variant(variant,p)
        assert not [f for f in audit_scenes(scenes,p) if f.severity=='error']
    from studio.deeppresenter import CompositionEnvironment
    env=CompositionEnvironment(p,plans)
    assert 'divider' in env.allowed


def test_native_title_only_layout_retained(template):
    from studio.native_template import native_patterns
    prs=Presentation(template)
    divider=prs.slides.add_slide(prs.slide_layouts[5]);divider.shapes.title.text='Раздел'
    assert any(p.source_slide==4 and p.role=='divider' for p in native_patterns(prs))


def test_safe_area_avoids_bottom_decoration():
    image=Image.new('RGB',(400,200),'white')
    # A pixel fixture, not a generated presentation asset.
    image.paste((30,40,50),(0,150,400,200))
    box=safe_body_zone(image,Box(x=0,y=0,w=400,h=200),scale=1)
    assert box.y+box.h<=150
    assert box.w==400


def test_table_repair_does_not_split_long_header_word(prepared):
    from studio.fonts import table_cell_fits,element_font
    _,_,p=prepared
    e=Element(kind='table',box=Box(x=80,y=150,w=400,h=250),rows=[['Официальные ролики','Фанатские публикации'],['12','18']],
        font=p.template.font,size=32,color=p.template.foreground)
    s=SlideScene(title='Таблица',background=p.template.background,elements=[e],source_ids=[],layout='table')
    repair_scenes([s],p)
    assert e.size<32
    assert table_cell_fits(e.rows[0][0],element_font(p.template,e)[1],e.size,184,113,True)
