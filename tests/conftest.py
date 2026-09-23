from pathlib import Path
import pytest
from pptx import Presentation
from pptx.util import Inches,Pt
from pptx.dml.color import RGBColor
from studio.config import Settings
from studio.store import Store
from studio.pipeline import prepare,load_package

@pytest.fixture
def template(tmp_path):
    prs=Presentation()
    prs.slide_width=Inches(13.333);prs.slide_height=Inches(7.5)
    for i in range(3):
        slide=prs.slides.add_slide(prs.slide_layouts[6])
        slide.background.fill.solid();slide.background.fill.fore_color.rgb=RGBColor.from_string("FFFFFF")
        for y,size,text in [(0.5,32,"OLD PRIVATE TITLE"),(2,20,"OLD PRIVATE CONTENT")]:
            shape=slide.shapes.add_textbox(Inches(.6),Inches(y),Inches(10),Inches(1))
            r=shape.text_frame.paragraphs[0].add_run();r.text=text
            r.font.name="Play";r.font.size=Pt(size);r.font.color.rgb=RGBColor.from_string("154A67")
    path=tmp_path/"unknown.pptx";prs.save(path)
    return path

@pytest.fixture
def content():
    return "# Проект\n"+"\n".join(f"## Этап {i}\nПодразделение {i} обрабатывает заявки через единый интерфейс." for i in range(1,13))

@pytest.fixture
def prepared(tmp_path,template,content):
    import shutil
    settings=Settings(data_dir=tmp_path/"data")
    store=Store(settings.data_dir)
    job=store.create("preparation",{"template_name":"unknown.pptx"})
    shutil.copyfile(template,store.directory(job["id"])/"input.pptx")
    prepare(store,job["id"],content,"Команда","",5)
    assert store.get(job["id"])["state"]=="ready",store.get(job["id"])
    return settings,store,load_package(store,job["id"])
