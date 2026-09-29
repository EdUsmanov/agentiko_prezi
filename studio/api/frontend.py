"""Serve the shared web UI in front of either API implementation."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp


WEB = Path(__file__).resolve().parents[2] / "web"


def register_frontend(app: FastAPI) -> FastAPI:
    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app


def create_frontend_app(backend: ASGIApp) -> FastAPI:
    app = register_frontend(FastAPI(title="VK Forma"))
    app.mount("/", backend, name="backend")
    return app
