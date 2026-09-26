import asyncio
from datetime import datetime, timezone
from pathlib import Path
import json
import shutil
import subprocess
import time
from pydantic import ValidationError
from .artifacts import package_results
from .config import ROOT, Settings
from .models import PreparedPackage, Finding, ContextualAudit, UploadedImage
from .security import digest, InputRejected
from .template import analyze_template
from .content import parse_content, parse_constraints
from .opendesign import export_design, provenance
from .gateway import ModelGateway
from .planner import plan, validate_plans, assign_compositions
from .composer import compose_variant
from .audit import audit_scenes, repair_scenes
from .render import render_variant
from .author import expand_brief
from .embedded_fonts import check_glyphs
from .fonts import role_font
from .native_template import compile_backgrounds
from .analysis import prepare_intelligence
from .diversity import ensure_diversity
from .security_gate import PromptInjectionDetected, check_text_fields, check_template, check_package

def preparation_diagnostics(template, warnings):
    notices=[]
    for message in warnings:
        if message.startswith("Внешняя ссылка исключена"):
            notices.append({"code":"external_link_blocked","severity":"info",
                "message":"Внешние ссылки отключены политикой безопасности. Их содержимое не загружалось. Связанные внешние ресурсы, если они были, не попадут в результат."})
        elif (message.startswith("Шрифт ") and "точное локальное начертание" in message) or message.startswith("Шрифты Microsoft:"):
            # Compatibility facts remain in font-model.json. They are not
            # actionable diagnostics for a user who supplied this template.
            continue
        else:
            notices.append({"code":"preparation_warning","severity":"warning","message":message})
    return notices

def grounded_review(raw,plans):
    parsed=ContextualAudit.model_validate(raw)
    slides={(v.key,i):s for v in plans.variants for i,s in enumerate(v.slides,1)}
    for finding in parsed.findings:
        slide=slides.get((finding.variant,finding.slide))
        if slide is None or finding.title_quote!=slide.title or not set(finding.fact_ids)<=set(slide.fact_ids):
            raise ValueError("Замечание critic не привязано к фактическому слайду")
    return parsed.model_dump()

def revision():
    try:
        return subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,stderr=subprocess.DEVNULL,text=True,timeout=2).strip()
    except Exception:
        return "uncommitted"

def versions():
    files=list((ROOT/"prompts").glob("*.md"))+list((ROOT/"config").glob("*.json"))+list((ROOT/"config").glob("*.yaml"))+list((ROOT/"studio").rglob("*.py"))+list((ROOT/"studio").rglob("*.mjs"))
    return {str(f.relative_to(ROOT)):digest(f.read_bytes()) for f in files}

def prepare(store,job_id,text,audience,instructions,slides,settings=None,content_model=None,base_constraints=None):
    started=time.monotonic()
    timings={}
    directory=store.directory(job_id)
    gateway = None
    package = None
    store.update(job_id,"running",phase="Разбор PPTX и дизайн-системы",progress=15)
    try:
        path=directory/"input.pptx"
        check_text_fields(content=text,audience=audience,instructions=instructions,
            canonical_content=content_model.model_dump_json() if content_model is not None else "")
        if content_model is not None and content_model.quarantined:
            raise PromptInjectionDetected(content_model.quarantined)
        from .cache_version import atomic_json
        atomic_json(directory/'analysis-input.json',{'text':text,'audience':audience,'instructions':instructions,
            'slides':slides,'content_model':content_model.model_dump() if content_model is not None else None,
            'base_constraints':base_constraints.model_dump() if base_constraints is not None else None})
        (directory/'analysis-input.json').chmod(0o600)
        check_template(path)
        settings=settings or Settings(data_dir=store.root)
        from .template_cache import TemplateCache
        template_cache=TemplateCache(settings)
        cached_template=template_cache.restore(path,directory)
        template=(cached_template[0] if cached_template else analyze_template(path,directory,
            allow_download=settings.download_open_fonts,
            font_progress=lambda phase:store.update(job_id,phase=phase,progress=30)))
        if any(f.get("required_for_generation",True) for f in template.missing_fonts) or not template.font_file:
            # Preserve technical analysis and canonical input; no LLM or rendering yet.
            content=content_model.model_copy(deep=True) if content_model is not None else parse_content(text)
            resume={"text":text,"audience":audience,"instructions":instructions,"slides":slides,
                "content_model":content.model_dump(),
                "base_constraints":base_constraints.model_dump() if base_constraints is not None else None}
            raw=json.dumps(resume,ensure_ascii=False)
            (directory/"font-resume.json").write_text(raw)
            (directory/"font-resume.json").chmod(0o600)
            (directory/"technical-profile.json").write_text(template.model_dump_json(indent=2))
            store.update(job_id,"waiting_fonts",phase="Нужны файлы шрифтов",progress=40,
                missing_fonts=[f for f in template.missing_fonts if f.get("required_for_generation",True)], resume_hash=digest(raw.encode()),template_hash=template.sha256,
                error="Добавьте точные TTF в fonts/ или data/local-fonts/ и нажмите «Проверить шрифты повторно». Анализ шаблона сохранён.")
            return
        if not cached_template and any(p.title_zone for p in template.patterns):
            store.update(job_id,phase="Подготовка исходных макетов и фирменной графики",progress=40)
            compile_backgrounds(template,path,directory)
        timings['technical_template_seconds']=round(time.monotonic()-started,3)
        # Keep original user-visible filename, never use it as a filesystem path.
        template.name=store.get(job_id).get("template_name",path.name)
        store.update(job_id,phase="Факты, таблицы и ограничения",progress=65)
        # Revision reuses trusted immutable canonical content, not lossy Markdown.
        content=content_model.model_copy(deep=True) if content_model is not None else parse_content(text)
        from .font_fallback import ensure_text_fonts, content_text
        ensure_text_fonts(template,content_text(content),directory)
        check_glyphs(role_font(template,"title")[1],content.title)
        check_glyphs(template.font_file,"\n".join(f.text for f in content.facts)+"\n"+
            "\n".join(cell for t in content.tables for row in [t.headers]+t.rows for cell in row))
        constraints=parse_constraints(slides,audience,instructions)
        if base_constraints is not None and constraints.count_mode=="default":
            constraints.slides=base_constraints.slides
            constraints.count_mode=base_constraints.count_mode
        if base_constraints is not None:
            constraints.size_preset=base_constraints.size_preset
            constraints.summarize=base_constraints.summarize
            constraints.confirm_plan=base_constraints.confirm_plan
            constraints.include_cover=base_constraints.include_cover
        if not template.font_file:
            raise InputRejected(template.warnings[-1])
        manifest={"template_sha256":template.sha256,"content_sha256":digest(content.model_dump_json().encode() if content_model is not None else text.encode()),
            "content_hash_format":"canonical_json" if content_model is not None else "original_text",
            "template_layers":{str(p.relative_to(directory)):digest(p.read_bytes()) for p in
                [*[Path(p.background_image) for p in template.patterns if p.background_image],
                 *([Path(template.background_source)] if template.background_source else [])]},
            "font":{"name":template.font,**template.font_origin},
            "constraints_sha256":digest(constraints.model_dump_json().encode()),"versions":versions(),
            "git_commit":revision(),"opendesign":provenance(),"analysis_seconds":round(time.monotonic()-started,3),
            "security":"typed composition operations only; no shell/file/network tools; XML/ZIP validation; no remote assets"}
        package=PreparedPackage(id=job_id,created_at=datetime.now(timezone.utc).isoformat(),template=template,
            content=content,original_content=content.model_copy(deep=True),constraints=constraints,manifest=manifest,
            images=[UploadedImage.model_validate(a) for a in json.loads((directory/"images.json").read_text())] if (directory/"images.json").exists() else [])
        manifest["uploaded_images"]=[{"id":a.id,"sha256":a.sha256,"width":a.width,"height":a.height} for a in package.images]
        package.manifest["uploaded_images"]=manifest["uploaded_images"]
        package.manifest["image_security"]={"metadata":"stripped","planning":"pixels_not_sent_to_llm",
            "vision":"read_only_final_audit_only","external_image_urls":False}
        settings=settings or Settings(data_dir=store.root)
        gateway=ModelGateway(settings)
        progress=lambda phase,value:store.update(job_id,phase=phase,progress=value)
        if cached_template:package.analysis['_template_snapshot']=cached_template[1]
        async def analyze():
            try:return await prepare_intelligence(package,path,gateway,progress)
            finally:
                if hasattr(gateway,'aclose'):await gateway.aclose()
        analysis_started=time.monotonic()
        package=asyncio.run(analyze())
        timings['intelligence_seconds']=round(time.monotonic()-analysis_started,3)
        checks_started=time.monotonic()
        for pattern in package.template.patterns:
            if pattern.reference_image:
                reference=Path(pattern.reference_image)
                package.manifest['template_layers'][str(reference.relative_to(directory))]=digest(reference.read_bytes())
        package.analysis["image_security"]={"accepted":len(package.images),"metadata":"removed",
            "ocr":"not_run","pixels_in_planning":False,"placement":"server_owned",
            "visual_review":"advisory_only; cannot override deterministic checks"}
        # Recheck model-authored labels before any final composition measurement.
        template=package.template
        ensure_text_fonts(template,content_text(package.content,package.prepared_plans),directory)
        package.manifest["font"]={"name":template.font,**template.font_origin}
        # Check model-authored titles/proposals against the actual selected font too.
        check_glyphs(template.font_file,"\n".join(f.text for f in package.content.facts))
        progress("Проверка композиций и фиксация пакета",95)
        if package.prepared_plans:
            check_glyphs(role_font(template,"title")[1],"\n".join(s.title for v in package.prepared_plans.variants for s in v.slides))
            decks={v.key:compose_variant(v,package) for v in package.prepared_plans.variants}
            for scenes in decks.values():
                repair_scenes(scenes,package)
            package.analysis["composition_preview"]=ensure_diversity(decks,package)
        else:
            package.analysis["composition_preview"]={'verified':False,'findings':[],
                'reason':'slide_budget_requires_input'}
        package.analysis["model_calls"]=gateway.calls
        package.analysis["model_usage"]=gateway.usage
        package.analysis['induction_errors'] = getattr(gateway, 'induction_errors', [])
        package.analysis["warnings"].extend(f["message"] for f in package.analysis["composition_preview"]["findings"])
        from .font_disclosure import preparation_substitutions, warnings as font_warnings
        package.manifest['font_substitutions'] = preparation_substitutions(package)
        package.analysis['font_substitutions'] = package.manifest['font_substitutions']
        package.analysis['warnings'].extend(font_warnings(package.manifest['font_substitutions'], planned=True))
        package.manifest["analysis_seconds"]=round(time.monotonic()-started,3)
        export_design(template,directory)
        from .diagnostics import stage_summary
        timings['local_final_checks_seconds']=round(time.monotonic()-checks_started,3)
        package.analysis['timings']={**timings,'model':stage_summary(gateway.calls)}
        report=package.analysis
        (directory/"analysis.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
        raw=package.model_dump_json(indent=2)
        (directory/"package.json").write_text(raw)
        warnings=template.warnings+package.content.warnings+package.analysis["warnings"]
        store.update(job_id,"ready",phase="Готово к генерации",progress=100,package_hash=digest(raw.encode()),
            auto_generation="needs_confirmation" if (report.get("slide_budget") or {}).get("status")=="needs_input" or (not constraints.confirm_plan and (report.get("slide_budget") or {}).get("status")=="adjusted") else "scheduled",
            auto_generate_at=time.time()+60,
            template=template.model_dump(exclude={"font_file","assets","font_assets","background_source"}) | {
                "font_assets":[{k:v for k,v in a.items() if k!="path"} for a in template.font_assets]},
            content={"title":package.content.title,"facts":len(package.content.facts),"tables":len(package.content.tables),"images":len(package.images)},
            constraints=constraints.model_dump(),warnings=warnings,analysis=report,
            diagnostics=preparation_diagnostics(template,warnings),
            quarantine=content.quarantined,analysis_seconds=package.manifest["analysis_seconds"])
    except PromptInjectionDetected as exc:
        store.update(job_id,"failed",error=str(exc),phase="Материалы заблокированы проверкой безопасности",
                     security_violation=exc.public(),quarantine=exc.findings)
    except Exception as exc:
        from .diagnostics import exception
        exception("analysis.failed",exc)
        message=str(exc) if isinstance(exc,(ValueError,InputRejected)) and not isinstance(exc,ValidationError) else "Ошибка анализа ("+type(exc).__name__+")"
        diagnostic = {'phase': store.get(job_id).get('phase'), 'error_type': type(exc).__name__,
            'model_calls': getattr(gateway, 'calls', []),
            'induction_errors': getattr(gateway, 'induction_errors', []),
            'template_semantics': package.analysis.get('template_semantics') if package else None,
            'editorial_repair': package.analysis.get('editorial_repair_diagnostics') if package else None,
            'elapsed_seconds': round(time.monotonic()-started, 3)}
        from .cache_version import atomic_json
        atomic_json(directory/'failure-report.json', diagnostic)
        (directory/'failure-report.json').chmod(0o600)
        from .induction import InductionFailure
        if isinstance(exc,InductionFailure):
            stages={'editorial':'составления плана слайдов','editorial_outline':'выбора структуры презентации',
                    'editorial_repair':'исправления конкретных слайдов','editorial_slides':'подготовки содержания слайдов','editorial_review':'проверки смысла',
                    'template_analyst':'анализа шаблона'}
            stage=getattr(exc,'stage','');label=stages.get(stage,stage or 'анализа')
            reason=('ответ модели превысил допустимый размер' if str(exc)=='ModelResponseTruncated' else
                    'модель не завершила запрос за отведённое время' if str(exc)=='TimeoutError' else
                    'не удалось соединиться с провайдером модели после повторных попыток' if str(exc) in ('ConnectError','ConnectTimeout','ReadError','WriteError','RemoteProtocolError') else
                    'модель не вернула проверяемый ответ')
            message=f'На этапе {label} {reason}. Проверенные результаты сохранены; повторный анализ использует их.'
        else:
            message += f" Этап: {diagnostic['phase']}; причина: {type(exc).__name__}."
        store.update(job_id,"failed",error=message,phase="Анализ остановлен",failure_diagnostics=diagnostic)

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
    for asset in package.template.font_assets:
        if not Path(asset["path"]).is_file() or digest(Path(asset["path"]).read_bytes()) != asset["sha256"]:
            raise ValueError("Начертание шрифта изменено после анализа. Повторите подготовку.")
    for asset in package.images:
        path=Path(asset.path)
        if not path.resolve().is_relative_to((store.root/"jobs").resolve()) or not path.is_file() or digest(path.read_bytes())!=asset.sha256:
            raise ValueError("Изображение изменено после анализа. Повторите подготовку.")
    for relative,sha in package.manifest.get("template_layers",{}).items():
        layer=store.directory(package_id)/relative
        if not layer.is_file() or digest(layer.read_bytes())!=sha:
            raise ValueError("Фоновый слой изменён после анализа. Повторите подготовку.")
    check_package(package,store.directory(package_id)/"input.pptx")
    return package

async def review_exported_content(plans,package,directory,gateway,timeout):
    from .export_audit import slide_text
    from .powerpoint import open_presentation
    try:
        facts={f.id:f for f in package.content.facts};slides=[]
        for variant in plans.variants:
            exported=open_presentation(directory/variant.key/'deck.pptx')
            slides.extend({'variant':variant.key,'slide':i,'title':plan.title,
                'actual_text':slide_text(output),'facts':[facts[f].model_dump() for f in plan.fact_ids]}
                for i,(plan,output) in enumerate(zip(variant.slides,exported.slides),1))
        from .content_review import review
        return await review(slides,plans,gateway,'critic',timeout,{
            'constraints':package.constraints.model_dump(),
            'slide_budget_adjustment':package.analysis.get('slide_budget')})
    except Exception as exc:
        return {'status':'failed','reason':type(exc).__name__,'findings':[]}


async def generate(store,job_id,settings):
    gateway=ModelGateway(settings)
    try:return await _generate(store,job_id,settings,gateway)
    finally:
        if hasattr(gateway,'aclose'):await gateway.aclose()


async def _generate(store,job_id,settings,gateway):
    started=time.monotonic();mark=started;timings={}
    def checkpoint(name):
        nonlocal mark
        now=time.monotonic();timings[name]=round(now-mark,3);mark=now
    job=store.get(job_id);directory=store.directory(job_id)
    package=load_package(store,job["package_id"])
    if package.analysis.get('slide_budget', {}).get('status') == 'needs_input':
        raise ValueError(package.analysis['slide_budget']['message'])
    if package.template.analysis_version<9:
        raise ValueError("Обновлён анализ шаблонов: повторите анализ материалов перед новой генерацией. "
            "Старые результаты сохранены; новый анализ определяет смысловые блоки и архетипы по общему каталогу.")
    deadline=job["deadline_at"]
    def remaining(reserve=0):
        value=deadline-time.time() if deadline is not None else float("inf")
        if value<=0:
            raise TimeoutError("Общий лимит генерации исчерпан")
        if value<=reserve:
            raise ValueError("Недостаточно оставшегося времени для стадии и обязательного экспорта. Неполный результат не выдаётся за готовый.")
        return value-reserve
    store.update(job_id,"running",phase="Планирование трёх вариантов",progress=8)
    author_warning=None
    if package.prepared_plans and package.analysis.get("model_mode")==settings.mode:
        plans=assign_compositions(validate_plans(package.prepared_plans,package),package)
        analysis=package.analysis
        model_preparation_failed=(analysis.get('planning_status')=='degraded' or
            analysis.get('template_semantics',{}).get('status') in ('partial','failed') or
            analysis.get('document_structure',{}).get('status') in ('degraded','failed'))
        fallback=("; ".join(analysis.get("warnings",[])) or "Модельная подготовка завершилась не полностью") if model_preparation_failed else None
        planning_source=package.analysis["planning_source"]
    else:
        # Backward-compatible path for immutable packages created before semantic preparation.
        plans,fallback=await plan(package,gateway,min(120,remaining(60)))
        planning_source="extractive" if fallback or settings.mode=="extractive" else "model"
    engine_report={"engine":"native","status":"completed"}
    if settings.engine=="deeppresenter":
        from .deeppresenter import design
        store.update(job_id,phase="DeepPresenter: выбор и проверка композиций",progress=20)
        # Reserve export + final audit inside the SAME server deadline for all variants.
        budget=None if deadline is None else min(90,remaining(150))
        try:
            plans,engine_report=await design(package,plans,gateway,directory/"design-runtime",budget)
        except TimeoutError as exc:
            remaining()  # Distinguish sub-stage timeout from the overall 300-second deadline.
            raise ValueError(f"DeepPresenter не успел завершить выбор композиций за бюджет отдельного запроса. "
                "Модель не ответила вовремя или этап не завершён. Повторите генерацию: повторный анализ не нужен. "
                "Движок не заменялся автоматически.") from exc
        except Exception as exc:
            raise ValueError("DeepPresenter не завершил проверенную композицию ("+type(exc).__name__+"). Подмена движка не выполнялась.") from exc
    checkpoint('planning_and_design_seconds')
    (directory/"generation-content.json").write_text(package.content.model_dump_json(indent=2))
    (directory/"plans.json").write_text(plans.model_dump_json(indent=2))
    store.update(job_id,phase="Вёрстка, аудит и экспорт",progress=35)
    source=store.directory(package.id)/"input.pptx"
    from .background_diversity import diversify_backgrounds
    from .composer import CompositionSession
    composition_cache=CompositionSession(package)
    decks={};background_selection={}
    for i,variant in enumerate(plans.variants):
        selected,scenes,report=diversify_backgrounds(variant,package,composition_cache)
        plans.variants[i]=selected;decks[selected.key]=scenes;background_selection[selected.key]=report
    package.analysis['background_diversity']=background_selection
    (directory/"plans.json").write_text(plans.model_dump_json(indent=2))
    initial_by_key={key:audit_scenes(scenes,package) for key,scenes in decks.items()}
    repairs_by_key={key:repair_scenes(scenes,package) for key,scenes in decks.items()}
    store.update(job_id,phase="Проверяем читаемость и различия трёх вариантов",progress=42)
    diversity=ensure_diversity(decks,package)
    def build(variant):
        remaining()
        scenes=decks[variant.key]
        initial=initial_by_key[variant.key]
        repairs=repairs_by_key[variant.key]
        findings=audit_scenes(scenes,package)
        remaining()
        rendering=render_variant(scenes,package.template,source,directory/variant.key)
        from .export_audit import audit_export
        return {"key":variant.key,"title":variant.title,"slides":len(scenes),
            "export_findings":audit_export(directory/variant.key/'deck.pptx',variant,package),
            "rendering":rendering,"template_strategies":sorted({s.strategy for s in scenes}),
            "findings":[f.model_dump() for f in findings],"repairs":[f.model_dump() for f in repairs],
            "initial_errors":sum(f.severity=="error" for f in initial),"scene":scenes}
    store.update(job_id,phase="Создаём файлы PPTX, PDF и предпросмотр трёх презентаций",progress=50)
    checkpoint('composition_and_local_audit_seconds')
    results=await asyncio.gather(*[asyncio.to_thread(build,v) for v in plans.variants])
    checkpoint('render_and_export_seconds')
    store.update(job_id,phase="Сверяем готовые презентации с исходным материалом",progress=88)
    contextual={"status":"not_run","reason":"Модель не подключена; контекстуальная оценка не имитируется","findings":[]}
    if settings.mode=="api" and remaining()>35:
        store.update(job_id,phase="Проверяем смысл и факты в тексте с помощью модели",progress=90)
        contextual=await review_exported_content(plans,package,directory,gateway,180 if deadline is None else min(25,remaining(10)))
    for result in results:
        result.pop("scene")
    from .visual import review_visuals
    visual=await review_visuals(results,directory,gateway,None if deadline is None else min(100,remaining(65)),
        lambda phase:store.update(job_id,phase=phase,progress=91),package)
    (directory/'visual-audit-initial.json').write_text(json.dumps(visual,ensure_ascii=False,indent=2))
    checkpoint('initial_model_reviews_seconds')
    from .refinement import refine
    plans,decks,results,visual,refinement=await refine(package,plans,decks,results,visual,directory,source,
        gateway,None if deadline is None else min(110,max(0,remaining()-30)),lambda phase:store.update(job_id,phase=phase,progress=94))
    if refinement.get('accepted'):
        contextual=await review_exported_content(plans,package,directory,gateway,180 if deadline is None else min(20,remaining(10)))
    checkpoint('refinement_seconds')
    (directory/'refinement.json').write_text(json.dumps(refinement,ensure_ascii=False,indent=2))
    (directory/'visual-audit.json').write_text(json.dumps(visual,ensure_ascii=False,indent=2))
    (directory/'plans.json').write_text(plans.model_dump_json(indent=2))
    from .export_audit import audit_export
    from .semantic_bindings import binding_report
    for result,variant in zip(results,plans.variants):
        result['export_findings']=audit_export(directory/variant.key/'deck.pptx',variant,package)
        result['findings'].extend(result.pop('export_findings'))
        result['repairs'].extend(result['rendering'].get('object_repairs',[]))
        result['semantic_bindings']=[binding_report(plan,package,pattern) if pattern else
            {'status':'general','reason':'Token composition has no source field contract',
             'archetype':plan.purpose,'pattern_id':None,'fields':[]}
            for plan,scene in zip(variant.slides,decks[variant.key])
            for pattern in [next((p for p in package.template.patterns if p.id==scene.pattern_id),None)]]
        for binding,scene in zip(result['semantic_bindings'],decks[variant.key]):
            if binding['archetype'] in ('comparison','process','timeline') and any(e.role=='user_image' for e in scene.elements):
                binding.update(status='general',reason='Media uses a shared authored content area')
    # Repair may change geometry. Re-check diversity without mutating reviewed slides.
    from .export_audit import content_geometry_signature
    from .powerpoint import open_presentation
    signatures={v.key:content_geometry_signature(open_presentation(directory/v.key/'deck.pptx'),
        [s.title for s in v.slides]) for v in plans.variants}
    diversity.update(signatures=signatures,distinct=len(set(signatures.values())),
        verified=len(set(signatures.values()))==len(decks),method='exported_content_geometry')
    from .quality import meaningful_diversity
    from .export_audit import content_scenes
    exported_decks={v.key:content_scenes(open_presentation(directory/v.key/'deck.pptx'),v,package)
                    for v in plans.variants}
    meaningful = meaningful_diversity(exported_decks, package.template)
    diversity.update(meaningful=meaningful, verified=diversity['verified'] and meaningful['verified'])
    from .background_diversity import background_report
    diversity['within_decks']={key:background_report(scenes,package.template) for key,scenes in decks.items()}
    warnings=list(package.content.warnings)+[f["message"] for f in diversity["findings"]]
    from .font_disclosure import unique, warnings as font_warnings
    font_substitutions = unique([item for result in results
        for item in result['rendering'].get('font_substitutions', [])])
    warnings.extend(font_warnings(font_substitutions))
    if package.analysis.get('slide_budget', {}).get('message'):
        warnings.append(package.analysis['slide_budget']['message'])
    if fallback:
        warnings.append(fallback)
    if author_warning:
        warnings.append(author_warning)
    native_preview=all(r["rendering"]["native_render"] for r in results)
    template_degraded=any("native_template" not in r["template_strategies"] or "token_composition" in r["template_strategies"] for r in results)
    if not native_preview:
        warnings.append("LibreOffice не найден: предпросмотр построен из модели сцены, а не из готового PPTX.")
    if template_degraded:
        warnings.append("Часть слайдов не использует исходные макеты. Соответствие шаблону требует проверки.")
    if settings.mode=="extractive":
        warnings.append("Автономный экстрактивный режим: LLM/VLM не использовались. Результат не доказывает качество модельного режима.")
    model_degraded=settings.mode=="api" and bool(fallback or author_warning or planning_source not in ("model","explicit_author_storyboard","semantic_summary_storyboard") or contextual["status"]!="completed")
    if model_degraded:
        warnings.append("Модельный путь выполнен не полностью: результат требует проверки, даже если геометрия корректна.")
    if visual["status"]!="completed":
        warnings.append("Визуальная проверка изображений слайдов не выполнена полностью. "+visual.get("reason",""))
    binding_degraded=any(b['archetype'] in ('comparison','process','timeline') and b['status']!='specialized'
        for r in results for b in r.get('semantic_bindings',[]))
    if binding_degraded:
        warnings.append('Для части специальных архетипов нет однозначной привязки к полям макета. Полный текст сохранён в общем поле; проверьте представление материала.')
    errors=sum(f["severity"]=="error" for r in results for f in r["findings"])+sum(f["severity"]=="error" for f in contextual["findings"])+sum(f["severity"]=="error" for f in visual["findings"])
    manifest={"run_id":job_id,"package_id":package.id,"package_hash":store.get(package.id)["package_hash"],
        "input_manifest":package.manifest,"generation_versions":versions(),"git_commit":revision(),
        "model_proposal_count":sum(f.source=="model_proposal" for f in package.content.facts),
        "model":{"mode":settings.mode,"id":settings.model_id or None,"parameters_b":settings.parameters_b,
            "license":settings.license,"stage":settings.stage,"usage":gateway.usage,
            "thinking_requested":settings.thinking,"structured_output":settings.structured_output,"calls":gateway.calls},
        "model_degraded":model_degraded,
        "engine":engine_report,
        "planning_source":planning_source,"composition_diversity":diversity,
        "preparation_analysis":package.analysis,
        "font_substitutions":font_substitutions,
        "started_at":job["created"],"deadline_at":deadline,"variants":results,
        "contextual_audit":contextual,"visual_audit":visual,"refinement":refinement,"warnings":warnings,"errors":errors,
        "checks":{"native_pptx_reopened":True,"pdf_pages":True,"html_live_dom":True,
            "native_pptx_render":native_preview,"powerpoint_visual_check":False,"ocr_check":False}}
    from .diagnostics import stage_summary
    checkpoint('final_local_audits_seconds')
    manifest['timings']={**timings,'model':stage_summary(gateway.calls),
        'composition_cache':{'hits':composition_cache.hits,'misses':composition_cache.misses}}
    from .quality import quality_report
    manifest["quality_report"] = quality_report(manifest)
    store.update(job_id,phase="Упаковываем готовые презентации и отчёт для скачивания",progress=96)
    (directory/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    from .quality_gate import require_publishable
    require_publishable(manifest)
    (directory/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    package_results(directory)
    remaining()
    elapsed=time.time()-job["created"]
    manifest["elapsed_seconds"]=round(elapsed,3)
    manifest["elapsed_scope"]="through_first_zip; API elapsed_seconds includes final repack and is authoritative"
    manifest["within_deadline"]=settings.deadline_seconds is None or elapsed<=settings.deadline_seconds
    (directory/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    # Repack to include final measured manifest; final API elapsed includes this too.
    package_results(directory)
    remaining()
    needs_review = manifest['quality_report']['status'] != 'passed_checks'
    store.update(job_id,"needs_review" if needs_review else "completed",phase="Требуется проверка" if needs_review else "Три презентации готовы",progress=100,
        elapsed_seconds=round(time.time()-job["created"],3),analysis_seconds=package.manifest.get("analysis_seconds"),
        variants=results,warnings=warnings,font_substitutions=font_substitutions,
        contextual_audit=contextual,visual_audit=visual,errors=errors,within_deadline=True,model_mode=settings.mode,
        refinement=refinement,quality_report=manifest["quality_report"],
        native_pptx_render=native_preview,composition_diversity=diversity,
        model_degraded=model_degraded,planning_source=manifest["planning_source"],engine=engine_report)
