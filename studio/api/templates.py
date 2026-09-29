from typing import Literal

from fastapi import APIRouter, File, Form, UploadFile

from .dependencies import ServiceDep


router = APIRouter(prefix="/api")


async def chunks(upload):
    while chunk := await upload.read(1024 * 1024):
        yield chunk


@router.post("/templates/analyze", status_code=202)
async def analyze_template(
    service: ServiceDep,
    reference_id: str = Form(""),
    template: UploadFile | None = File(None),
):
    return await service.analyze_template(
        reference_id=reference_id,
        template_name=template.filename if template else "",
        template_chunks=chunks(template) if template else None,
    )


@router.post("/prepare", status_code=202)
async def prepare(
    service: ServiceDep,
    text: str = Form(..., max_length=120000),
    audience: str = Form("", max_length=2000),
    instructions: str = Form("", max_length=5000),
    slides: int | None = Form(None, ge=1, le=30),
    size_preset: Literal["mini", "standard", "large"] | None = Form(None),
    reference_id: str = Form(""),
    template_job_id: str = Form(""),
    template: UploadFile | None = File(None),
    images: list[UploadFile] | None = File(None),
    input_mode: Literal["content", "brief"] = Form("content"),
    image_presentation: Literal["plain", "device"] = Form("plain"),
):
    uploads = [image for image in (images or []) if image.filename]
    return await service.prepare(
        text=text,
        audience=audience,
        instructions=instructions,
        slides=slides,
        size_preset=size_preset,
        reference_id=reference_id,
        template_job_id=template_job_id,
        template_name=template.filename if template else "",
        template_chunks=chunks(template) if template else None,
        images=[(image.filename, image.read) for image in uploads],
        input_mode=input_mode,
        image_presentation=image_presentation,
    )
