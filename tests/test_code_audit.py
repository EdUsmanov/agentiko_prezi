"""Regressions for lifetime and configuration findings from the main audit."""

import asyncio
import sqlite3
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from studio import app as api
from studio import presentation_service as application_module
from studio.jobs import runtime as runtime_module
from studio.config import Settings
from studio.jobs.store import Store


def test_database_connection_closes_and_transaction_rolls_back(tmp_path):
    store = Store(tmp_path)
    with store.connect() as connection:
        connection.execute("CREATE TABLE audit_test (value TEXT)")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    with pytest.raises(RuntimeError):
        with store.connect() as connection:
            connection.execute("INSERT INTO audit_test VALUES ('not committed')")
            raise RuntimeError("abort")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    with store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM audit_test").fetchone()[0] == 0


def test_shutdown_cannot_publish_late_preparation_or_rearm_autostart(tmp_path, monkeypatch):
    processes = []

    class Child:
        pid = 123456
        returncode = None

        def __init__(self):
            self.stopped = asyncio.Event()
            self.stdout = asyncio.StreamReader()
            processes.append(self)

        async def wait(self):
            await self.stopped.wait()
            return self.returncode

        def kill(self):
            self.returncode = -9
            self.stdout.feed_eof()
            self.stopped.set()

    async def spawn(*args, **kwargs):
        return Child()

    monkeypatch.setattr(runtime_module.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(runtime_module, "kill_worker", lambda process: process.kill())
    application = api.create_app(Settings(data_dir=tmp_path))

    async def check():
        try:
            async with application.router.lifespan_context(application):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=application), base_url="http://testserver"
                ) as client:
                    response = await client.post(
                        "/api/prepare",
                        data={"text": "Материал"},
                        files={"template": ("sample.pptx", b"fake")},
                    )
                    assert response.status_code == 202
                    jid = response.json()["id"]
                    for _ in range(100):
                        if processes:
                            break
                        await asyncio.sleep(0.01)
                    assert processes
            job = application.state.store.get(jid)
            assert job["state"] == "cancelled"
            assert job.get("auto_generation") != "scheduled"
            assert processes[0].returncode == -9
            application.state.store.update(jid, "ready", auto_generation="scheduled")
            assert application.state.store.get(jid)["state"] == "cancelled"
        finally:
            for process in processes:
                process.kill()

    asyncio.run(check())


def test_unexpected_preparation_failure_reaches_terminal_state(tmp_path, monkeypatch):
    async def crash(*args, **kwargs):
        raise OSError("test-only storage failure")

    monkeypatch.setattr(runtime_module.asyncio, "create_subprocess_exec", crash)
    application = api.create_app(Settings(data_dir=tmp_path))
    with TestClient(application) as client:
        response = client.post(
            "/api/prepare", data={"text": "Материал"}, files={"template": ("sample.pptx", b"fake")}
        )
        assert response.status_code == 202
        jid = response.json()["id"]
        for _ in range(100):
            job = application.state.store.get(jid)
            if job["state"] not in ("accepted", "running"):
                break
            time.sleep(0.01)
        assert job["state"] == "failed"
        assert any(
            e["event"] == "worker.supervisor_failed" for e in application.state.store.events(jid)
        )


def test_generation_refuses_mixed_loaded_and_disk_code(tmp_path, monkeypatch):
    monkeypatch.setattr(application_module, "pipeline_version", lambda: "loaded")
    application = api.create_app(Settings(data_dir=tmp_path))
    with TestClient(application) as client:
        monkeypatch.setattr(
            application.state.presentation_service, "version_operation", lambda: "changed"
        )
        response = client.post("/api/generate", json={"package_id": "a" * 32})
        assert response.status_code == 503
        assert not application.state.store.recent()


def test_worker_settings_preserve_server_configuration(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        mode="api",
        base_url="https://openrouter.ai/api/v1",
        model_id="test-model",
        api_key="test-credential-only",
        parameters_b=27,
        open_weights=True,
        license="Apache-2.0",
        engine="deeppresenter",
        visual_review=True,
        download_fonts=False,
        thinking=None,
        thinking_token_budget=1024,
        openrouter_providers=("test-provider",),
        openrouter_allow_fallbacks=True,
        model_concurrency=3,
        deadline_seconds=123,
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "stale-test-key")
    monkeypatch.setenv("STUDIO_MODEL_THINKING", "true")
    for key, value in settings.worker_environment().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        "studio.config.load_env", lambda: pytest.fail("worker must use frozen server settings")
    )
    assert Settings.from_worker_env() == settings


def test_cancel_does_not_overwrite_completed_preparation(tmp_path):
    store = Store(tmp_path)
    job = store.create("preparation")
    store.update(job["id"], "ready", auto_generation="scheduled")
    assert not store.cancel_active(job["id"])
    assert store.get(job["id"])["state"] == "ready"


def test_child_process_receives_exact_settings_snapshot(tmp_path, monkeypatch):
    import subprocess
    import sys

    settings = Settings(
        data_dir=tmp_path,
        visual_review=True,
        thinking=None,
        thinking_token_budget=768,
        api_key="not-a-real-test-key",
        openrouter_providers=("provider-for-test",),
        download_fonts=False,
    )
    monkeypatch.setenv("STUDIO_MODEL_THINKING", "true")
    child = """
from studio.config import Settings
s=Settings.from_worker_env()
assert s.visual_review is True and s.thinking is None
assert s.thinking_token_budget==768 and s.download_fonts is False
assert s.api_key=='not-a-real-test-key' and s.openrouter_providers==('provider-for-test',)
print('server settings preserved')
"""
    result = subprocess.run(
        [sys.executable, "-c", child],
        env=settings.worker_environment(),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "server settings preserved"
