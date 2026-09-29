from fastapi import APIRouter
from fastapi.responses import FileResponse

from .dependencies import ServiceDep
from .schemas import ApproveRequest, DraftRequest, GenerateRequest, RepairRequest, ReviseRequest


router = APIRouter(prefix="/api")


@router.post("/generate", status_code=202)
async def generate(body: GenerateRequest, service: ServiceDep):
    return service.generate(
        body.package_id, accept_adjusted_slide_count=body.accept_adjusted_slide_count
    )


@router.post("/packages/{pid}/auto-generation/cancel")
def cancel_auto_generation(pid: str, service: ServiceDep):
    return service.cancel_auto_generation(pid)


@router.post("/packages/{pid}/retry-fonts", status_code=202)
async def retry_fonts(pid: str, service: ServiceDep):
    return service.retry_fonts(pid)


@router.post("/packages/{pid}/revise", status_code=202)
async def revise(pid: str, body: ReviseRequest, service: ServiceDep):
    return service.revise(
        pid, instructions=body.instructions, slides=body.slides, size_preset=body.size_preset
    )


@router.get("/packages/{pid}/draft")
def draft(pid: str, service: ServiceDep):
    return service.get_draft(pid)


@router.post("/packages/{pid}/draft", status_code=202)
async def update_draft(pid: str, body: DraftRequest, service: ServiceDep):
    return service.update_draft(pid, package_hash=body.package_hash, draft=body.draft)


@router.post("/packages/{pid}/approve")
async def approve(pid: str, body: ApproveRequest, service: ServiceDep):
    return service.approve_brief(pid, package_hash=body.package_hash, draft_hash=body.draft_hash)


@router.get("/generations/{gid}/findings")
def findings(gid: str, service: ServiceDep):
    return service.findings(gid)


@router.get("/generations/{gid}/preview/{variant}/{slide}")
def preview(gid: str, variant: str, slide: int, service: ServiceDep):
    return FileResponse(service.preview_path(gid, variant, slide), media_type="image/png")


@router.post("/generations/{gid}/repair", status_code=202)
async def repair(gid: str, body: RepairRequest, service: ServiceDep):
    return service.repair(gid, audit_hash=body.audit_hash, finding_ids=body.finding_ids)
