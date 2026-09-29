"""HTTP composition root for the presentation studio."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware

from studio.api import errors, jobs, presentations, system, templates
from studio.presentation_service import PresentationService, ApplicationError
from studio.api.middleware import UploadLimitMiddleware, local_security
from studio.config import Settings
from studio.api.frontend import register_frontend
from studio.diagnostics import configure
from studio.providers.gateway import validate_model_policy
from studio.providers.selection import select_startup_provider
from studio.jobs.runtime import JobRuntime
from studio.security_gate import PromptInjectionDetected
from studio.jobs.store import Store
from studio.contents.uploads import MAX_TOTAL_BYTES


def create_app(settings=None):
    settings = settings or Settings.from_env()
    validate_model_policy(settings)
    configure(settings.api_key)
    configure(settings.fallback_model_api_key)
    store = Store(settings.data_dir)
    runtime = JobRuntime(settings, store)
    service = PresentationService(settings, store, runtime)
    runtime.start_generation = service.generate
    runtime.on_prepared = service.preparation_finished

    @asynccontextmanager
    async def lifespan(app):
        selected = await select_startup_provider(settings)
        app.state.settings = selected
        runtime.settings = selected
        service.settings = selected
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

    return register_frontend(app)
