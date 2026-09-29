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
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator
from .config import Settings, ROOT
from .store import Store
from .pipeline import prepare, load_package
from .gateway import validate_model_policy
from .uploads import (
    sanitize_image,
    bind_image_sections,
    MAX_IMAGES,
    MAX_IMAGE_BYTES,
    MAX_TOTAL_BYTES,
)
from .cache_version import pipeline_version
from .examples import sources
from .security_gate import PromptInjectionDetected, check_text_fields


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_id: str
    accept_adjusted_slide_count: bool = False
    variant_count: Literal[1, 3] = 3

    @field_validator("variant_count", mode="before")
    @classmethod
    def strict_variant_count(cls, value):
        if type(value) is not int or value not in (1, 3):
            raise ValueError("Выберите одну или три презентации")
        return value


class ReviseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instructions: str = Field(min_length=1, max_length=5000)
    slides: int | None = Field(default=None, ge=1, le=30)
    size_preset: Literal["mini", "standard", "large"] | None = None


def kill_worker(process):
    # Worker is started in a dedicated session; include its LibreOffice children.
    if getattr(process, "returncode", None) is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    except PermissionError:
        # Some hosts permit signalling our child but not its process group.
        # Do not crash application shutdown for that narrower restriction.
        import logging

        logging.getLogger(__name__).warning(
            "Group cleanup denied; stopping owned worker PID %s", process.pid
        )
        try:
            process.kill()
        except ProcessLookupError:
            pass


class UploadLimitMiddleware:
    def __init__(self, app, max_bytes):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > self.max_bytes:
                raise HTTPException(413, "Превышен размер запроса")
            return message

        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
            if length < 0:
                raise ValueError()
        except ValueError:
            return await JSONResponse({"detail": "Некорректный Content-Length"}, status_code=400)(
                scope, receive, send
            )
        if length > self.max_bytes:
            return await JSONResponse({"detail": "Превышен размер запроса"}, status_code=413)(
                scope, receive, send
            )
        await self.app(scope, limited_receive, send)


def create_app(settings=None):
    settings = settings or Settings.from_env()
    validate_model_policy(settings)
    from .diagnostics import configure

    configure(settings.api_key)
    store = Store(settings.data_dir)
    processes = {}
    tasks = set()
    prep_slots = asyncio.Semaphore(2)
    loaded_pipeline_version = pipeline_version()

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        for job in store.scheduled():
            spawn(auto_generate(job["id"]))
        yield
        for process in processes.values():
            if process.returncode is None:
                try:
                    kill_worker(process)
                except ProcessLookupError:
                    pass
        pending = list(tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    app = FastAPI(title="VK Forma Presentation Studio", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.settings = settings
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(
        UploadLimitMiddleware, max_bytes=settings.max_upload_bytes + MAX_TOTAL_BYTES + 512 * 1024
    )

    @app.middleware("http")
    async def local_security(request, call_next):
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("sec-fetch-site") == "cross-site" or (
                origin and urlsplit(origin).netloc != request.headers.get("host")
            ):
                return JSONResponse({"detail": "Cross-origin writes are not allowed"}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if request.url.path.endswith("deck.html"):
            response.headers["Content-Security-Policy"] = (
                "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"
            )
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Объект не найден"}, 404)

    @app.exception_handler(PromptInjectionDetected)
    async def injection_rejected(request, exc):
        return JSONResponse({"detail": exc.public()}, status_code=422)

    def spawn(coro):
        task = asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def run_prepare(
        jid, text, audience, instructions, slides, content_model=None, base_constraints=None
    ):
        try:
            async with prep_slots:
                from .diagnostics import scope

                def analyze():
                    with scope(store, jid):
                        prepare(
                            store,
                            jid,
                            text,
                            audience,
                            instructions,
                            slides,
                            settings,
                            content_model,
                            base_constraints,
                        )

                await asyncio.to_thread(analyze)
                job = store.get(jid)
                if job["state"] == "ready":
                    budget = job.get("analysis", {}).get("slide_budget") or {}
                    # prepare() persists the countdown with ready. Do not re-arm it:
                    # a concurrent UI request may already have cancelled or started it.
                    if job.get("auto_generation") == "needs_confirmation":
                        store.log(jid, "generation.confirmation_required", status=budget["status"])
                    elif job.get("auto_generation") == "scheduled":
                        store.log(jid, "generation.scheduled", delay_seconds=60)
                        spawn(auto_generate(jid))
        except asyncio.CancelledError:
            # asyncio.to_thread keeps running after task cancellation. The
            # terminal guard prevents its late update from publishing a result.
            store.cancel_active(jid)
            raise
        except Exception as exc:
            from .diagnostics import scope, exception

            with scope(store, jid):
                exception("preparation.supervisor_failed", exc)
            store.update(
                jid,
                "failed",
                error="Не удалось завершить анализ. Подробности в журнале.",
                phase="Анализ остановлен",
            )

    async def auto_generate(pid):
        job = store.get(pid)
        await asyncio.sleep(max(0, job["auto_generate_at"] - time.time()))
        if store.get(pid).get("auto_generation") != "scheduled":
            return
        try:
            await generation(
                GenerateRequest(
                    package_id=pid,
                    accept_adjusted_slide_count=bool(
                        job.get("constraints", {}).get("confirm_plan")
                    ),
                ),
                automatic=True,
            )
        except Exception as exc:
            from .diagnostics import redact

            store.update(
                pid, auto_generation="blocked", auto_error=redact(str(getattr(exc, "detail", exc)))
            )
            store.log(
                pid,
                "generation.autostart_failed",
                level="error",
                message=str(getattr(exc, "detail", exc)),
            )

    def require_current_pipeline():
        if pipeline_version() != loaded_pipeline_version:
            raise HTTPException(
                503,
                "Код пайплайна обновлён. Перезапустите приложение перед новым анализом или генерацией.",
            )

    async def supervise(job):
        process = None
        reader = None
        try:
            env = settings.worker_environment()
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "studio.worker",
                job["id"],
                str(settings.data_dir),
                cwd=ROOT,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            processes[job["id"]] = process
            from .diagnostics import capture_stream

            reader = asyncio.create_task(capture_stream(store, job["id"], process.stdout))
            await asyncio.wait_for(
                process.wait(),
                max(0.01, job["deadline_at"] - time.time())
                if job.get("deadline_at") is not None
                else None,
            )
            current = store.get(job["id"])
            if current["state"] in ("accepted", "running"):
                store.update(job["id"], "failed", error="Рабочий процесс завершился без результата")
        except asyncio.TimeoutError:
            store.update(
                job["id"],
                "timed_out",
                phase="Время истекло",
                error="Истёк явно заданный административный лимит задания",
            )
            if process and process.returncode is None:
                kill_worker(process)
                await process.wait()
        except asyncio.CancelledError:
            if process and process.returncode is None:
                try:
                    kill_worker(process)
                except ProcessLookupError:
                    pass
            if store.get(job["id"])["state"] in ("accepted", "running"):
                store.update(job["id"], "cancelled", error="Запуск прерван")
            raise
        except Exception as exc:
            from .diagnostics import scope, exception

            with scope(store, job["id"]):
                exception("worker.supervisor_failed", exc)
            store.update(job["id"], "failed", error="Не удалось запустить рабочий процесс")
        finally:
            if process:
                if process.returncode is None:
                    kill_worker(process)
                await process.wait()
            if reader:
                await asyncio.gather(reader, return_exceptions=True)
            processes.pop(job["id"], None)

    @app.post("/api/packages/{pid}/auto-generation/cancel")
    def cancel_auto_generation(pid: str):
        try:
            return store.cancel_auto_generation(pid)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/jobs/{jid}/diagnostics")
    def diagnostics(jid: str, after: int = -1, download: bool = False):
        from .diagnostics import redact

        job = store.get(jid)
        attachments = {}
        for name in (
            "failure-report.json",
            "pptagent-private.log",
            "background-model.json",
            "text-zones.json",
        ):
            path = store.directory(jid) / name
            if path.is_file():
                with path.open("rb") as source:
                    source.seek(max(0, path.stat().st_size - 64000))
                    attachments[name] = redact(source.read(64000).decode("utf-8", "replace"))
        # Keep checks available in the journal and its download, including old jobs.
        checks = {
            key: job[key]
            for key in (
                "diagnostics",
                "warnings",
                "quality_report",
                "contextual_audit",
                "visual_audit",
                "refinement",
                "composition_diversity",
                "font_substitutions",
                "missing_fonts",
                "auto_error",
            )
            if key in job
        }
        checks["variants"] = [
            {
                key: variant[key]
                for key in ("key", "title", "findings", "repairs", "audit_scope")
                if key in variant
            }
            for variant in job.get("variants", [])
        ]
        checks = json.loads(redact(json.dumps(checks, ensure_ascii=False)))
        result = {
            "job_id": jid,
            "state": job["state"],
            "phase": job.get("phase"),
            "error": redact(job.get("error", "")),
            "events": store.events(
                jid, max(0, after), 10000 if download else 500, latest=after < 0 and not download
            ),
            "checks": checks,
            "attachments": attachments,
            "retention": "Последние 10 000 событий; файлы — последние 64 КБ. Ключи скрыты, запросы и ответы модели целиком не записываются.",
        }
        return JSONResponse(
            result,
            headers={"Content-Disposition": f'attachment; filename="diagnostics-{jid}.json"'}
            if download
            else {},
        )

    @app.get("/api/health")
    def health():
        from .deeppresenter import readiness

        return {
            "status": "ok",
            "model_mode": settings.mode,
            "model_id": settings.model_id or None,
            "engine": settings.engine,
            "deeppresenter": readiness(),
            "deadline_seconds": settings.deadline_seconds,
            "reference_count": len(sources(settings)),
            "features": {
                "native_pptx": True,
                "html": True,
                "pdf": True,
                "ocr": False,
                "vlm": settings.mode == "api" and settings.visual_review,
                "t2i": False,
                "semantic_preparation": True,
                "deterministic_compositions": True,
                "organizer_preanalysis": True,
                "font_roles": True,
                "download_fonts": settings.download_fonts,
                "image_uploads": True,
            },
        }

    @app.get("/api/references")
    def references():
        return sources(settings)

    @app.get("/api/references/{reference_id}/profile")
    def saved_reference_profile(reference_id: str):
        from .reference_analysis import reference_profile

        try:
            return reference_profile(settings, reference_id)
        except KeyError as exc:
            raise HTTPException(404, "Неизвестный шаблон") from exc

    @app.get("/api/runtime")
    def runtime_status():
        return {
            "restart_required": pipeline_version() != loaded_pipeline_version,
            "organizer_preanalysis": True,
        }

    @app.get("/api/jobs")
    def jobs():
        return store.recent()

    @app.get("/api/jobs/{jid}")
    def job(jid: str):
        return store.get(jid)

    @app.post("/api/prepare", status_code=202)
    async def preparation(
        text: str = Form(..., max_length=120000),
        audience: str = Form("", max_length=2000),
        instructions: str = Form("", max_length=5000),
        slides: int | None = Form(None, ge=1, le=30),
        size_preset: Literal["mini", "standard", "large"] | None = Form(None),
        reference_id: str = Form(""),
        template: UploadFile | None = File(None),
        images: list[UploadFile] | None = File(None),
    ):
        images = [image for image in (images or []) if image.filename]
        check_text_fields(
            content=text,
            audience=audience,
            instructions=instructions,
            template_name=template.filename if template else "",
            image_names="\n".join(i.filename or "" for i in images),
        )
        require_current_pipeline()
        if len(images) > MAX_IMAGES:
            raise HTTPException(422, "Допускается до 12 изображений")
        if bool(reference_id) == bool(template and template.filename):
            raise HTTPException(422, "Выберите один шаблон: файл или пример")
        if not text.strip():
            raise HTTPException(422, "Добавьте текст")
        ref = None
        if reference_id:
            ref = next((r for r in references() if r["id"] == reference_id), None)
            if not ref:
                raise HTTPException(404, "Неизвестный пример")
        if (
            template
            and template.filename
            and not template.filename.lower().endswith((".pptx", ".potx"))
        ):
            raise HTTPException(422, "Поддерживаются PPTX и POTX без макросов")
        job = store.create(
            "preparation", {"template_name": ref["name"] if ref else Path(template.filename).name}
        )
        target = store.directory(job["id"]) / "input.pptx"
        if ref:
            shutil.copyfile(settings.data_dir / "references" / ref["id"] / "input.pptx", target)
        else:
            size = 0
            with target.open("wb") as out:
                while chunk := await template.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        store.update(job["id"], "failed", error="Шаблон превышает 60 МБ")
                        raise HTTPException(413, "Шаблон превышает 60 МБ")
                    out.write(chunk)
        assets = []
        total = 0
        try:
            for image in images:
                raw = await image.read(MAX_IMAGE_BYTES + 1)
                total += len(raw)
                if total > MAX_TOTAL_BYTES:
                    raise ValueError("Суммарный размер изображений превышает 24 МБ")
                asset = await asyncio.to_thread(
                    sanitize_image, raw, image.filename, store.directory(job["id"]), len(assets) + 1
                )
                if any(a.name.casefold() == asset.name.casefold() for a in assets):
                    raise ValueError("У картинок должны быть разные имена файлов")
                assets.append(asset)
            assets = bind_image_sections(assets, text)
            (store.directory(job["id"]) / "images.json").write_text(
                json.dumps([a.model_dump() for a in assets], ensure_ascii=False)
            )
        except ValueError as exc:
            store.update(job["id"], "failed", error=str(exc))
            raise HTTPException(422, str(exc)) from exc
        from .content import parse_constraints

        base = (
            parse_constraints(slides, audience, instructions, size_preset) if size_preset else None
        )
        spawn(run_prepare(job["id"], text, audience, instructions, slides, base_constraints=base))
        return store.get(job["id"])

    @app.post("/api/generate", status_code=202)
    async def generate_request(body: GenerateRequest):
        return await generation(body)

    async def generation(body: GenerateRequest, automatic=False):
        require_current_pipeline()
        if settings.engine == "deeppresenter":
            from .deeppresenter import readiness

            if not readiness()["ready"]:
                raise HTTPException(
                    503, "DeepPresenter не готов: установите зависимости deeppresenter"
                )
        try:
            package = load_package(store, body.package_id)
            budget = package.analysis.get("slide_budget", {})
            if budget.get("status") == "needs_input":
                raise ValueError(budget["message"])
            if (
                budget.get("status") == "adjusted" or package.constraints.confirm_plan
            ) and not body.accept_adjusted_slide_count:
                raise ValueError(
                    budget.get("message", "План подготовлен.")
                    + " Подтвердите генерацию с предложенным количеством слайдов."
                )
            job, created = store.generation_for(
                body.package_id, automatic=automatic, variant_count=body.variant_count
            )
            if not created:
                return job or store.get(body.package_id)
            deadline = (
                job["created"] + settings.deadline_seconds
                if settings.deadline_seconds is not None
                else None
            )
            store.update(
                job["id"],
                deadline_at=deadline,
                slide_count_decision={
                    "mode": "automatic" if automatic else "confirmed",
                    "count": package.analysis.get("planned_slides", package.constraints.slides),
                    "message": (
                        "Система предложила и автоматически выбрала оптимальное количество слайдов"
                        if automatic
                        else "Пользователь подтвердил предложенный план"
                    ),
                },
            )
            job = store.get(job["id"])
        except PromptInjectionDetected:
            raise
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        spawn(supervise(job))
        return job

    @app.post("/api/packages/{pid}/retry-fonts", status_code=202)
    async def retry_fonts(pid: str):
        require_current_pipeline()
        from .security import digest
        from .models import ContentModel, Constraints

        job = store.get(pid)
        if job["kind"] != "preparation" or job["state"] != "waiting_fonts":
            raise HTTPException(409, "Задание не ожидает шрифтов")
        directory = store.directory(pid)
        raw = (directory / "font-resume.json").read_bytes()
        if (
            digest(raw) != job["resume_hash"]
            or digest((directory / "input.pptx").read_bytes()) != job["template_hash"]
        ):
            raise HTTPException(409, "Сохранённый вход изменён. Загрузите материалы повторно.")
        saved = json.loads(raw)
        # No await between state check/update: duplicate requests cannot start two workers.
        store.update(pid, "accepted", error=None, phase="Повторная проверка шрифтов")
        spawn(
            run_prepare(
                pid,
                saved["text"],
                saved["audience"],
                saved["instructions"],
                saved["slides"],
                ContentModel.model_validate(saved["content_model"]),
                Constraints.model_validate(saved["base_constraints"])
                if saved["base_constraints"]
                else None,
            )
        )
        return store.get(pid)

    @app.post("/api/packages/{pid}/revise", status_code=202)
    async def revise(pid: str, body: ReviseRequest):
        check_text_fields(instructions=body.instructions)
        require_current_pipeline()
        package = load_package(store, pid)
        new = store.create(
            "preparation",
            {
                "template_name": package.template.name,
                "parent_package": pid,
                "change_explanation": "Создана новая версия входных ограничений. Исходный пакет и результаты сохранены.",
            },
        )
        shutil.copyfile(
            store.directory(pid) / "input.pptx", store.directory(new["id"]) / "input.pptx"
        )
        # Immutable normalized image assets are shared by reference, not re-decoded.
        (store.directory(new["id"]) / "images.json").write_text(
            json.dumps([a.model_dump() for a in package.images], ensure_ascii=False)
        )
        # Revisions always start from immutable original evidence, not the previous summary.
        store.cancel_auto_generation(pid)
        from .content import parse_constraints

        base = parse_constraints(
            body.slides, package.constraints.audience, body.instructions, body.size_preset
        )
        if body.slides is None and body.size_preset is None and base.count_mode == "default":
            base = package.constraints.model_copy(deep=True)
        base.summarize = base.confirm_plan = True
        spawn(
            run_prepare(
                new["id"],
                "",
                package.constraints.audience,
                body.instructions,
                body.slides,
                package.original_content or package.content,
                base,
            )
        )
        return store.get(new["id"])

    @app.get("/api/jobs/{jid}/files/{filename:path}")
    def artifact(jid: str, filename: str):
        job = store.get(jid)
        if job["state"] not in ("ready", "completed", "needs_review", "waiting_fonts"):
            raise HTTPException(409, "Артефакты ещё не готовы")
        root = store.directory(jid)
        allowed = {
            "manifest.json",
            "presentations.zip",
            "plans.json",
            "DESIGN.md",
            "tokens.json",
            "opendesign.json",
            "analysis.json",
        }
        allowed |= {
            "font-model.json",
            "layout-font-model.json",
            "color-model.json",
            "background-model.json",
            "text-zones.json",
        }
        if job["state"] == "waiting_fonts" and filename not in {
            "font-model.json",
            "layout-font-model.json",
        }:
            raise HTTPException(409, "Доступен только отчёт о шрифтах")
        allowed |= {
            f"{v}/{f}"
            for v in ("executive", "analytical", "story")
            for f in ("deck.pptx", "deck.pdf", "deck.html", "slides.json")
        }
        allowed |= {f"{v}/FONT_LICENSES.txt" for v in ("executive", "analytical", "story")}
        allowed |= {
            f"{v}/slide-{i}.png" for v in ("executive", "analytical", "story") for i in range(1, 31)
        }
        from .artifacts import public_path

        path = public_path(root, filename) if filename in allowed else None
        if path is None:
            raise HTTPException(404, "Файл не найден")
        inline = path.suffix in (".html", ".png", ".pdf")
        return FileResponse(path, filename=None if inline else path.name)

    @app.get("/")
    def index():
        return FileResponse(ROOT / "web/index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
    return app
