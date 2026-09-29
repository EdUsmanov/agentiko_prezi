"""HTTP composition root for the presentation studio."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api import errors, jobs, presentations, system, templates
from studio.presentation_service import PresentationService, ApplicationError
from .api.middleware import UploadLimitMiddleware, local_security
from .config import ROOT, Settings
from .diagnostics import configure
from studio.providers.gateway import validate_model_policy
from studio.jobs.runtime import JobRuntime
from .security_gate import PromptInjectionDetected
from studio.jobs.store import Store
from studio.contents.uploads import MAX_TOTAL_BYTES


def create_app(settings=None):
    settings = settings or Settings.from_env()
    validate_model_policy(settings)
    configure(settings.api_key)
    store = Store(settings.data_dir)
    runtime = JobRuntime(settings, store)
    service = PresentationService(settings, store, runtime)
    runtime.start_generation = service.generate
    runtime.on_prepared = service.preparation_finished

    @asynccontextmanager
    async def lifespan(app):
        await runtime.startup()
        try:
            yield
        finally:
            await runtime.shutdown()

    app = FastAPI(title="VK Forma Presentation Studio", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.runtime = runtime
    app.state.presentation_service = service
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(
        UploadLimitMiddleware, max_bytes=settings.max_upload_bytes + MAX_TOTAL_BYTES + 512 * 1024
    )

    app.middleware("http")(local_security)

    app.add_exception_handler(KeyError, errors.missing)
    app.add_exception_handler(PromptInjectionDetected, errors.injection_rejected)
    app.add_exception_handler(ApplicationError, errors.application_error)

    app.include_router(templates.router)
    app.include_router(presentations.router)
    app.include_router(jobs.router)
    app.include_router(system.router)

    @app.get("/")
    def index():
        return FileResponse(ROOT / "web/index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
    return app
