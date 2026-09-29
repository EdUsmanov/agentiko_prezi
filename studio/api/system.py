from fastapi import APIRouter
from urllib.parse import urlsplit

from studio.providers.deeppresenter import readiness
from studio.api.dependencies import ServiceDep


router = APIRouter(prefix="/api")


@router.get("/health")
def health(service: ServiceDep):
    settings = service.settings
    return {
        "status": "ok",
        "model_mode": settings.mode,
        "model_id": settings.model_id or None,
        "model_provider": urlsplit(settings.base_url).hostname if settings.mode == "api" else None,
        "engine": settings.engine,
        "deeppresenter": readiness(),
        "deadline_seconds": settings.deadline_seconds,
        "reference_count": len(service.references()),
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


@router.get("/references")
def references(service: ServiceDep):
    return service.references()


@router.get("/references/{reference_id}/profile")
def saved_reference_profile(reference_id: str, service: ServiceDep):
    return service.reference_profile(reference_id)


@router.get("/runtime")
def runtime_status(service: ServiceDep):
    return {"restart_required": service.restart_required(), "organizer_preanalysis": True}
