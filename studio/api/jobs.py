from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from studio.api.dependencies import ServiceDep


router = APIRouter(prefix="/api/jobs")


@router.get("")
def jobs(service: ServiceDep):
    return service.recent_jobs()


@router.get("/{jid}")
def job(jid: str, service: ServiceDep):
    return service.get_job(jid)


@router.delete("/{jid}")
def delete_job(jid: str, service: ServiceDep):
    return service.delete_job(jid)


@router.get("/{jid}/diagnostics")
def diagnostics(jid: str, service: ServiceDep, after: int = -1, download: bool = False):
    return JSONResponse(
        service.diagnostics(jid, after=after, download=download),
        headers={"Content-Disposition": f'attachment; filename="diagnostics-{jid}.json"'}
        if download
        else {},
    )


@router.get("/{jid}/files/{filename:path}")
def artifact(jid: str, filename: str, service: ServiceDep):
    path = service.artifact_path(jid, filename)
    inline = path.suffix in (".html", ".png", ".pdf")
    return FileResponse(path, filename=None if inline else path.name)


@router.post("/{jid}/cancel")
async def cancel_job(jid: str, service: ServiceDep):
    return await service.cancel_job(jid)
