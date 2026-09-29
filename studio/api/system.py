from fastapi import APIRouter

from studio.providers.deeppresenter import readiness
from .dependencies import ServiceDep


router = APIRouter(prefix="/api")


@router.get("/health")
def health(service: ServiceDep):
    settings = service.settings
    return {
        "status": "ok",
        "model_mode": settings.mode,
        "model_id": settings.model_id or None,
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
            "organizer_preanalysis": False,
            "font_roles": True,
            "download_fonts": settings.download_fonts,
            "image_uploads": True,
        },
    }


@router.get("/references")
def references(service: ServiceDep):
    return service.references()


@router.get("/runtime")
def runtime_status(service: ServiceDep):
    return {"restart_required": service.restart_required(), "organizer_preanalysis": False}
