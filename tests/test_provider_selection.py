"""Startup routing uses a real, bounded completion contract."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from studio.app import create_app
from studio.config import Settings
from studio.providers.selection import select_startup_provider


def configured(tmp_path):
    return Settings(
        data_dir=tmp_path,
        mode="api",
        base_url="https://openrouter.ai/api/v1",
        model_id="qwen/qwen3.8-27b",
        api_key="openrouter-test-key",
        fallback_model_base_url="https://api.neuraldeep.ru/v1",
        fallback_model_id="qwen3.8-27b-noreason",
        fallback_model_api_key="neuraldeep-test-key",
        parameters_b=27,
        open_weights=True,
        license="Apache-2.0",
        thinking=False,
    )


@pytest.mark.parametrize("primary_status", [200, 401, 429, 503, "timeout"])
def test_startup_uses_working_provider(tmp_path, primary_status):
    seen = []

    def respond(request):
        host = request.url.host
        seen.append(host)
        body = json.loads(request.content)
        assert "json" in body["messages"][0]["content"].lower()
        assert body["response_format"] == {"type": "json_object"}
        if host == "openrouter.ai":
            assert request.headers["authorization"] == "Bearer openrouter-test-key"
            assert body["model"] == "qwen/qwen3.8-27b"
            assert body["provider"]["only"] == ["alibaba"]
            assert body["reasoning"] == {"enabled": False}
            if primary_status == "timeout":
                raise httpx.ReadTimeout("simulated timeout")
            if primary_status != 200:
                return httpx.Response(primary_status)
        else:
            assert host == "api.neuraldeep.ru"
            assert request.headers["authorization"] == "Bearer neuraldeep-test-key"
            assert body["model"] == "qwen3.8-27b-noreason"
            assert body["chat_template_kwargs"] == {"enable_thinking": False}
            assert "provider" not in body and "reasoning" not in body
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    selected = asyncio.run(
        select_startup_provider(configured(tmp_path), transport=httpx.MockTransport(respond))
    )
    if primary_status == 200:
        assert seen == ["openrouter.ai"]
        assert selected.api_key == "openrouter-test-key"
    else:
        assert seen == ["openrouter.ai", "api.neuraldeep.ru"]
        assert selected.api_key == "neuraldeep-test-key"
        assert selected.model_id == "qwen3.8-27b-noreason"
        assert selected.fallback_model_api_key == ""


def test_startup_fails_when_both_providers_are_unavailable(tmp_path):
    attempted = []

    def fail(request):
        attempted.append(request.url.host)
        return httpx.Response(503)

    with pytest.raises(RuntimeError, match="OpenRouter и NeuralDeep недоступны"):
        asyncio.run(
            select_startup_provider(configured(tmp_path), transport=httpx.MockTransport(fail))
        )
    assert attempted == ["openrouter.ai", "api.neuraldeep.ru"]


def test_app_and_workers_share_selected_provider(tmp_path, monkeypatch):
    attempted = []

    async def probe(settings, *, transport=None):
        attempted.append(settings.base_url)
        if settings.base_url.startswith("https://openrouter.ai"):
            raise httpx.ConnectError("simulated outage")

    monkeypatch.setattr("studio.providers.selection.probe_completion", probe)
    application = create_app(configured(tmp_path))
    with TestClient(application) as client:
        assert client.get("/api/health").json()["model_id"] == "qwen3.8-27b-noreason"
        assert client.get("/api/health").json()["model_provider"] == "api.neuraldeep.ru"
        assert application.state.presentation_service.settings == application.state.runtime.settings
        worker_env = application.state.runtime.settings.worker_environment()
        monkeypatch.setenv("STUDIO_WORKER_SETTINGS", worker_env["STUDIO_WORKER_SETTINGS"])
        assert Settings.from_worker_env().api_key == "neuraldeep-test-key"
    assert attempted == ["https://openrouter.ai/api/v1", "https://api.neuraldeep.ru/v1"]


def test_partial_fallback_configuration_is_rejected_before_requests(tmp_path):
    from dataclasses import replace

    settings = replace(configured(tmp_path), fallback_model_api_key="")
    with pytest.raises(ValueError, match="адрес, модель и ключ"):
        asyncio.run(select_startup_provider(settings))


def test_fallback_credentials_are_loaded_separately(tmp_path, monkeypatch):
    from studio import config

    for key in list(config.os.environ):
        if key.startswith(("STUDIO_", "LLM_", "OPENROUTER_")):
            monkeypatch.delenv(key)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / ".env").write_text(
        "STUDIO_MODEL_MODE=api\n"
        "STUDIO_MODEL_BASE_URL=https://openrouter.ai/api/v1\n"
        "STUDIO_MODEL_ID=qwen/qwen3.8-27b\n"
        "OPENROUTER_API_KEY=router-only\n"
        "STUDIO_FALLBACK_MODEL_BASE_URL=https://api.neuraldeep.ru/v1/\n"
        "STUDIO_FALLBACK_MODEL_ID=qwen3.8-27b-noreason\n"
        "STUDIO_FALLBACK_MODEL_API_KEY=neural-only\n"
    )
    loaded = Settings.from_env()
    assert loaded.api_key == "router-only"
    assert loaded.fallback_model_api_key == "neural-only"
    assert loaded.fallback_model_base_url == "https://api.neuraldeep.ru/v1"
    assert "neural-only" not in repr(loaded)
