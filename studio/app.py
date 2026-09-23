import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time
from urllib.parse import urlsplit
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, ConfigDict
from .config import Settings, ROOT
from .store import Store
from .pipeline import prepare, load_package
from .gateway import validate_model_policy

class GenerateRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    package_id: str

class ReviseRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    instructions: str=Field(min_length=1,max_length=5000)
    slides: int | None=Field(default=None,ge=1,le=30)

def kill_worker(process):
    # Worker is started in a dedicated session; include its LibreOffice children.
    try:
        if os.name=="posix":
            os.killpg(process.pid,signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass

class UploadLimitMiddleware:
    def __init__(self,app,max_bytes):
        self.app=app;self.max_bytes=max_bytes
    async def __call__(self,scope,receive,send):
        if scope["type"]!="http":
            return await self.app(scope,receive,send)
        total=0
        async def limited_receive():
            nonlocal total
            message=await receive()
            total+=len(message.get("body",b""))
            if total>self.max_bytes:
                raise HTTPException(413,"Превышен размер запроса")
            return message
        headers=dict(scope.get("headers",[]))
        if int(headers.get(b"content-length",b"0"))>self.max_bytes:
            return await JSONResponse({"detail":"Превышен размер запроса"},status_code=413)(scope,receive,send)
        await self.app(scope,limited_receive,send)

def create_app(settings=None):
    settings=settings or Settings.from_env()
    validate_model_policy(settings)
    store=Store(settings.data_dir)
    processes={};tasks=set()
    prep_slots=asyncio.Semaphore(2)

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        yield
        for process in processes.values():
            if process.returncode is None:
                try:
                    kill_worker(process)
                except ProcessLookupError:
                    pass
        for task in tasks:
            task.cancel()

    app=FastAPI(title="Presentation Studio",version="0.1.0",lifespan=lifespan)
    app.state.store=store;app.state.settings=settings
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=["127.0.0.1","localhost","::1","testserver"])
    app.add_middleware(UploadLimitMiddleware,max_bytes=settings.max_upload_bytes+512*1024)

    @app.middleware("http")
    async def local_security(request,call_next):
        origin=request.headers.get("origin")
        if request.method not in ("GET","HEAD","OPTIONS"):
            if request.headers.get("sec-fetch-site")=="cross-site" or (origin and urlsplit(origin).netloc!=request.headers.get("host")):
                return JSONResponse({"detail":"Cross-origin writes are not allowed"},403)
        response=await call_next(request)
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="no-referrer"
        response.headers["Cache-Control"]="no-store"
        if request.url.path.endswith("deck.html"):
            response.headers["Content-Security-Policy"]="sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:"
        else:
            response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"
        return response

    @app.exception_handler(KeyError)
    async def missing(request,exc):
        return JSONResponse({"detail":"Объект не найден"},404)

    def spawn(coro):
        task=asyncio.create_task(coro);tasks.add(task);task.add_done_callback(tasks.discard)

    async def run_prepare(jid,text,audience,instructions,slides):
        async with prep_slots:
            await asyncio.to_thread(prepare,store,jid,text,audience,instructions,slides)

    async def supervise(job):
        process=None
        try:
            env=os.environ.copy()
            # Config is server-owned, never derived from an uploaded document.
            env.update({"STUDIO_MODEL_MODE":settings.mode,"STUDIO_MODEL_BASE_URL":settings.base_url,
                "STUDIO_MODEL_ID":settings.model_id,"STUDIO_MODEL_API_KEY":settings.api_key,
                "STUDIO_MODEL_PARAMETERS_B":str(settings.parameters_b),"STUDIO_MODEL_OPEN_WEIGHTS":str(settings.open_weights).lower(),
                "STUDIO_MODEL_LICENSE":settings.license,"STUDIO_STAGE":settings.stage,"STUDIO_VK_ALLOWED_HOSTS":",".join(settings.vk_hosts)})
            env["STUDIO_MODEL_STRUCTURED_OUTPUT"] = str(settings.structured_output).lower()
            env["STUDIO_MODEL_CONCURRENCY"] = str(settings.model_concurrency)
            if settings.thinking is not None:
                env["STUDIO_MODEL_THINKING"] = str(settings.thinking).lower()
            else:
                env.pop("STUDIO_MODEL_THINKING", None)
            process=await asyncio.create_subprocess_exec(sys.executable,"-m","studio.worker",job["id"],str(settings.data_dir),
                cwd=ROOT,env=env,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,start_new_session=True)
            processes[job["id"]]=process
            await asyncio.wait_for(process.wait(),max(.01,job["deadline_at"]-time.time()))
            current=store.get(job["id"])
            if current["state"] in ("accepted","running"):
                store.update(job["id"],"failed",error="Рабочий процесс завершился без результата")
        except asyncio.TimeoutError:
            store.update(job["id"],"timed_out",phase="Время истекло",error="Все три презентации не готовы за общий лимит 300 секунд")
            if process and process.returncode is None:
                kill_worker(process)
                await process.wait()
        except asyncio.CancelledError:
            if process and process.returncode is None:
                try:
                    kill_worker(process)
                except ProcessLookupError:
                    pass
            store.update(job["id"],"cancelled",error="Запуск прерван")
            raise
        except Exception:
            store.update(job["id"],"failed",error="Не удалось запустить рабочий процесс")
        finally:
            processes.pop(job["id"],None)

    @app.get("/api/health")
    def health():
        index=settings.data_dir/"references/index.json"
        return {"status":"ok","model_mode":settings.mode,"model_id":settings.model_id or None,
            "deadline_seconds":settings.deadline_seconds,"reference_count":len(json.loads(index.read_text())) if index.exists() else 0,
            "features":{"native_pptx":True,"html":True,"pdf":True,"ocr":False,"vlm":False,"t2i":False}}

    @app.get("/api/references")
    def references():
        path=settings.data_dir/"references/index.json"
        return json.loads(path.read_text()) if path.exists() else []

    @app.get("/api/jobs")
    def jobs():
        return store.recent()

    @app.get("/api/jobs/{jid}")
    def job(jid:str):
        return store.get(jid)

    @app.post("/api/prepare",status_code=202)
    async def preparation(text:str=Form(...,max_length=120000),audience:str=Form("",max_length=2000),
        instructions:str=Form("",max_length=5000),slides:int|None=Form(None,ge=1,le=30),
        reference_id:str=Form(""),template:UploadFile|None=File(None)):
        if bool(reference_id)==bool(template and template.filename):
            raise HTTPException(422,"Выберите один шаблон: файл или пример")
        if not text.strip():
            raise HTTPException(422,"Добавьте текст")
        ref=None
        if reference_id:
            ref=next((r for r in references() if r["id"]==reference_id),None)
            if not ref:
                raise HTTPException(404,"Неизвестный пример")
        if template and template.filename and not template.filename.lower().endswith(".pptx"):
            raise HTTPException(422,"Поддерживается PPTX")
        job=store.create("preparation",{"template_name":ref["name"] if ref else Path(template.filename).name})
        target=store.directory(job["id"])/"input.pptx"
        if ref:
            shutil.copyfile(settings.data_dir/"references"/ref["id"]/"input.pptx",target)
        else:
            size=0
            with target.open("wb") as out:
                while chunk:=await template.read(1024*1024):
                    size+=len(chunk)
                    if size>settings.max_upload_bytes:
                        store.update(job["id"],"failed",error="PPTX превышает 60 МБ")
                        raise HTTPException(413,"PPTX превышает 60 МБ")
                    out.write(chunk)
        spawn(run_prepare(job["id"],text,audience,instructions,slides))
        return store.get(job["id"])

    @app.post("/api/generate",status_code=202)
    async def generation(body:GenerateRequest):
        try:
            load_package(store,body.package_id)
            job=store.create("generation",{"package_id":body.package_id,"progress":0,"phase":"Запуск"})
            deadline=job["created"]+settings.deadline_seconds
            store.update(job["id"],deadline_at=deadline)
            job=store.get(job["id"])
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc
        spawn(supervise(job))
        return job

    @app.post("/api/packages/{pid}/revise",status_code=202)
    async def revise(pid:str,body:ReviseRequest):
        package=load_package(store,pid)
        lines=["# "+package.content.title]
        tables={t.id:t for t in package.content.tables}
        for f in package.content.facts:
            if f.section:
                lines.append("## "+f.section)
            if f.source in tables:
                t=tables[f.source]
                lines.extend(["| "+" | ".join(t.headers)+" |","| "+" | ".join("---" for _ in t.headers)+" |"])
                lines.extend("| "+" | ".join(row)+" |" for row in t.rows)
            else:
                lines.append(f.text)
        new=store.create("preparation",{"template_name":package.template.name,"parent_package":pid,
            "change_explanation":"Создана новая версия входных ограничений. Исходный пакет и результаты сохранены."})
        shutil.copyfile(store.directory(pid)/"input.pptx",store.directory(new["id"])/"input.pptx")
        # New instruction supersedes earlier instruction on conflict.
        spawn(run_prepare(new["id"],"\n\n".join(lines),package.constraints.audience,body.instructions,body.slides or package.constraints.slides))
        return store.get(new["id"])

    @app.get("/api/jobs/{jid}/files/{filename:path}")
    def artifact(jid:str,filename:str):
        job=store.get(jid)
        if job["state"] not in ("ready","completed","needs_review"):
            raise HTTPException(409,"Артефакты ещё не готовы")
        root=store.directory(jid)
        allowed={"manifest.json","presentations.zip","plans.json","DESIGN.md","tokens.json","opendesign.json"}
        allowed|={f"{v}/{f}" for v in ("executive","analytical","story") for f in ("deck.pptx","deck.pdf","deck.html","slides.json")}
        allowed|={f"{v}/slide-{i}.png" for v in ("executive","analytical","story") for i in range(1,31)}
        if filename not in allowed or not (root/filename).is_file():
            raise HTTPException(404,"Файл не найден")
        path=root/filename
        inline=path.suffix in (".html",".png",".pdf")
        return FileResponse(path,filename=None if inline else path.name)

    @app.get("/")
    def index():
        return FileResponse(ROOT/"web/index.html")

    app.mount("/static",StaticFiles(directory=ROOT/"web"),name="static")
    return app
