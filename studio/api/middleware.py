"""Request limits and browser security headers for the HTTP boundary."""

from urllib.parse import urlsplit

from fastapi import HTTPException
from fastapi.responses import JSONResponse


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
    if request.url.path.endswith(("deck.html", "evidence.html")):
        response.headers["Content-Security-Policy"] = (
            "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:"
        )
    else:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "font-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"
        )
    return response
