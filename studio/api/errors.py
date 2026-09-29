"""Map workflow failures to HTTP responses."""

from fastapi.responses import JSONResponse


def missing(request, exc):
    return JSONResponse({"detail": "Объект не найден"}, 404)


def injection_rejected(request, exc):
    return JSONResponse({"detail": exc.public()}, status_code=422)


def application_error(request, exc):
    status = {"invalid": 422, "missing": 404, "conflict": 409, "unavailable": 503, "too_large": 413}
    return JSONResponse({"detail": str(exc)}, status_code=status[exc.kind])
