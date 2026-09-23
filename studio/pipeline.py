import asyncio
from datetime import datetime, timezone
from pathlib import Path
import json
import shutil
import subprocess
import time
from zipfile import ZipFile, ZIP_DEFLATED
from .config import ROOT
from .models import PreparedPackage, Finding
from .security import digest, InputRejected
from .template import analyze_template
from .content import parse_content, parse_constraints
from .opendesign import export_design, provenance
from .gateway import ModelGateway
from .planner import plan
from .composer import compose_variant
from .audit import audit_scenes, repair_scenes
from .render import render_variant
from .author import expand_brief
from .embedded_fonts import check_glyphs

def revision():
    try:
        return subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,stderr=subprocess.DEVNULL,text=True,timeout=2).strip()
    except Exception:
        return "uncommitted"

def versions():
    files=list((ROOT/"prompts").glob("*.md"))+list((ROOT/"config").glob("*.json"))
    return {str(f.relative_to(ROOT)):digest(f.read_bytes()) for f in files}

def prepare(store,job_id,text,audience,instructions,slides):
    started=time.monotonic()
    directory=store.directory(job_id)
    store.update(job_id,"running",phase="Разбор PPTX и дизайн-системы",progress=15)
    try:
        path=directory/"input.pptx"
        template=analyze_template(path,directory)
        # Keep original user-visible filename, never use it as a filesystem path.
        template.name=store.get(job_id).get("template_name",path.name)
        store.update(job_id,phase="Факты, таблицы и ограничения",progress=65)
        content=parse_content(text)
        check_glyphs(template.font_file,content.title+"\n"+"\n".join(f.text for f in content.facts)+"\n"+
            "\n".join(cell for t in content.tables for row in [t.headers]+t.rows for cell in row))
        constraints=parse_constraints(slides,audience,instructions)
        if not template.font_file:
            raise InputRejected(template.warnings[-1])
        export_design(template,directory)
        manifest={"template_sha256":template.sha256,"content_sha256":digest(text.encode()),
            "font":{"name":template.font,**template.font_origin},
            "constraints_sha256":digest(constraints.model_dump_json().encode()),"versions":versions(),
            "git_commit":revision(),"opendesign":provenance(),"analysis_seconds":round(time.monotonic()-started,3),
            "security":"no tools for model; XML/ZIP validation; no remote assets"}
        package=PreparedPackage(id=job_id,created_at=datetime.now(timezone.utc).isoformat(),template=template,
            content=content,constraints=constraints,manifest=manifest)
        raw=package.model_dump_json(indent=2)
        (directory/"package.json").write_text(raw)
        store.update(job_id,"ready",phase="Готово к генерации",progress=100,package_hash=digest(raw.encode()),
            template=template.model_dump(exclude={"font_file","assets"}),
            content={"title":content.title,"facts":len(content.facts),"tables":len(content.tables)},
            constraints=constraints.model_dump(),warnings=template.warnings+content.warnings,
            quarantine=content.quarantined,analysis_seconds=manifest["analysis_seconds"])
    except Exception as exc:
        message=str(exc) if isinstance(exc,(ValueError,InputRejected)) else "Ошибка анализа ("+type(exc).__name__+")"
        store.update(job_id,"failed",error=message,phase="Анализ остановлен")

def load_package(store,package_id):
    job=store.get(package_id)
    if job["state"]!="ready":
        raise ValueError("Подготовка не завершена")
    raw=(store.directory(package_id)/"package.json").read_bytes()
    if digest(raw)!=job["package_hash"]:
        raise ValueError("Подготовленный пакет был изменён после анализа")
    package=PreparedPackage.model_validate_json(raw)
    if digest((store.directory(package_id)/"input.pptx").read_bytes())!=package.template.sha256:
        raise ValueError("Исходный шаблон изменён после анализа")
    font_hash=package.template.font_origin.get("sha256")
    if font_hash and digest(Path(package.template.font_file).read_bytes())!=font_hash:
        raise ValueError("Шрифт изменён после анализа. Повторите подготовку.")
    return package

async def generate(store,job_id,settings):
    job=store.get(job_id);directory=store.directory(job_id)
    package=load_package(store,job["package_id"])
    deadline=job["deadline_at"]
    def remaining(reserve=0):
        value=deadline-time.time()-reserve
        if value<=0:
            raise TimeoutError("Общий лимит генерации исчерпан")
        return value
    store.update(job_id,"running",phase="Планирование трёх вариантов",progress=8)
    gateway=ModelGateway(settings)
    package,author_warning=await expand_brief(package,gateway,min(45,remaining(100)))
    (directory/"generation-content.json").write_text(package.content.model_dump_json(indent=2))
    plans,fallback=await plan(package,gateway,min(120,remaining(60)))
    (directory/"plans.json").write_text(plans.model_dump_json(indent=2))
    store.update(job_id,phase="Вёрстка, аудит и экспорт",progress=35)
    source=store.directory(package.id)/"input.pptx"
    def build(variant):
        remaining()
        scenes=compose_variant(variant,package)
        initial=audit_scenes(scenes,package)
        repairs=repair_scenes(scenes,package)
        findings=audit_scenes(scenes,package)
        remaining()
        render_variant(scenes,package.template,source,directory/variant.key)
        return {"key":variant.key,"title":variant.title,"slides":len(scenes),
            "findings":[f.model_dump() for f in findings],"repairs":[f.model_dump() for f in repairs],
            "initial_errors":sum(f.severity=="error" for f in initial),"scene":scenes}
    results=await asyncio.gather(*[asyncio.to_thread(build,v) for v in plans.variants])
    store.update(job_id,phase="Проверка покрытия и упаковка",progress=88)
    contextual={"status":"not_run","reason":"Модель не подключена; контекстуальная оценка не имитируется","findings":[]}
    if settings.mode=="api" and remaining()>35:
        try:
            contextual_raw=await gateway.json_request("critic",{"source":package.content.model_dump(),"plans":plans.model_dump()},timeout=min(25,remaining(10)))
            if set(contextual_raw)!={"findings"} or not isinstance(contextual_raw["findings"],list):
                raise ValueError("Неверная схема аудита")
            contextual={"status":"completed","type":"text_model_review","findings":[Finding.model_validate(f).model_dump() for f in contextual_raw["findings"]]}
        except Exception as exc:
            contextual={"status":"failed","reason":type(exc).__name__,"findings":[]}
    for result in results:
        result.pop("scene")
    warnings=list(package.content.warnings)
    if fallback:
        warnings.append(fallback)
    if author_warning:
        warnings.append(author_warning)
    if settings.mode=="extractive":
        warnings.append("Автономный экстрактивный режим: LLM/VLM не использовались. Результат не доказывает качество модельного режима.")
    model_degraded=settings.mode=="api" and bool(fallback or author_warning or contextual["status"]!="completed")
    if model_degraded:
        warnings.append("Модельный путь выполнен не полностью: результат требует проверки, даже если геометрия корректна.")
    errors=sum(f["severity"]=="error" for r in results for f in r["findings"])+sum(f["severity"]=="error" for f in contextual["findings"])
    manifest={"run_id":job_id,"package_id":package.id,"package_hash":store.get(package.id)["package_hash"],
        "input_manifest":package.manifest,"generation_versions":versions(),"git_commit":revision(),
        "model_proposal_count":sum(f.source=="model_proposal" for f in package.content.facts),
        "model":{"mode":settings.mode,"id":settings.model_id or None,"parameters_b":settings.parameters_b,
            "license":settings.license,"stage":settings.stage,"usage":gateway.usage,
            "thinking_requested":settings.thinking,"structured_output":settings.structured_output,"calls":gateway.calls},
        "model_degraded":model_degraded,
        "planning_source":"extractive" if fallback or settings.mode=="extractive" else "model",
        "started_at":job["created"],"deadline_at":deadline,"variants":results,
        "contextual_audit":contextual,"warnings":warnings,"errors":errors,
        "checks":{"native_pptx_reopened":True,"pdf_pages":True,"html_live_dom":True,
            "powerpoint_visual_check":False,"ocr_check":False}}
    (directory/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    with ZipFile(directory/"presentations.zip","w",ZIP_DEFLATED) as z:
        for file in sorted(directory.rglob("*")):
            if file.is_file() and file.suffix in (".pptx",".pdf",".html",".json"):
                z.write(file,file.relative_to(directory))
    remaining()
    elapsed=time.time()-job["created"]
    manifest["elapsed_seconds"]=round(elapsed,3)
    manifest["within_deadline"]=elapsed<=settings.deadline_seconds
    (directory/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    # Repack to include final measured manifest; final API elapsed includes this too.
    with ZipFile(directory/"presentations.zip","w",ZIP_DEFLATED) as z:
        for file in sorted(directory.rglob("*")):
            if file.is_file() and file.suffix in (".pptx",".pdf",".html",".json"):
                z.write(file,file.relative_to(directory))
    remaining()
    needs_review=bool(errors or model_degraded)
    store.update(job_id,"needs_review" if needs_review else "completed",phase="Требуется проверка" if needs_review else "Три презентации готовы",progress=100,
        elapsed_seconds=round(time.time()-job["created"],3),variants=results,warnings=warnings,
        contextual_audit=contextual,errors=errors,within_deadline=True,model_mode=settings.mode,
        model_degraded=model_degraded,planning_source=manifest["planning_source"])

def index_examples(paths,settings):
    root=settings.data_dir/"references";root.mkdir(parents=True,exist_ok=True)
    results=[]
    for source in paths:
        source=Path(source)
        sha=digest(source.read_bytes())
        folder=root/sha[:20];folder.mkdir(exist_ok=True)
        target=folder/"input.pptx"
        shutil.copyfile(source,target)
        profile=analyze_template(target,folder)
        profile.name=source.name
        export_design(profile,folder)
        (folder/"profile.json").write_text(profile.model_dump_json(indent=2))
        results.append({"id":sha[:20],"name":source.name,"font":profile.font,"slides":profile.slide_count,
            "patterns":len(profile.patterns),"palette":profile.colors[:6]})
    (root/"index.json").write_text(json.dumps(results,ensure_ascii=False,indent=2))
    return results
