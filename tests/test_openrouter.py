import asyncio
import json
from dataclasses import replace
import httpx
import pytest
from studio.config import Settings
from studio.providers.gateway import ModelGateway, ModelPolicyError


def settings(tmp_path, **extra):
    return Settings(
        data_dir=tmp_path,
        mode="api",
        base_url="https://openrouter.ai/api/v1",
        model_id="qwen/qwen3.8-27b",
        api_key="new-router-key",
        parameters_b=27,
        open_weights=True,
        license="Apache-2.0",
        thinking=False,
        structured_output=True,
        **extra,
    )


@pytest.mark.parametrize(
    "thinking,stage,budget",
    [
        (False, "editorial", 0),
        (True, "editorial", 2048),
        (True, "template_analyst", 0),
        (True, "text_zone", 0),
    ],
)
def test_wire_routing_and_reasoning(tmp_path, thinking, stage, budget):
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }

    def response(request):
        body = json.loads(request.content)
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer new-router-key"
        assert body["model"] == "qwen/qwen3.8-27b"
        assert body["provider"] == {
            "only": ["alibaba"],
            "allow_fallbacks": False,
            "require_parameters": True,
        }
        assert body["reasoning"] == (
            {"enabled": True, "max_tokens": budget} if budget else {"enabled": False}
        )
        assert "chat_template_kwargs" not in body and "tools" not in body
        assert body["response_format"]["json_schema"]["schema"] == schema
        return httpx.Response(
            200,
            json={
                "provider": "Alibaba",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": '{"ok":true}',
                            "reasoning": None,
                            "reasoning_details": [],
                        },
                    }
                ],
            },
        )

    g = ModelGateway(
        replace(settings(tmp_path), thinking=thinking), transport=httpx.MockTransport(response)
    )

    async def run():
        try:
            assert await g.json_request(stage, {}, schema=schema) == {"ok": True}
        finally:
            await g.aclose()

    asyncio.run(run())
    assert g.calls[0]["provider"] == "Alibaba" and g.calls[0]["reasoning_characters"] == 0


def test_reasoning_diagnostics_do_not_persist_contents(tmp_path):
    raw = {
        "provider": "Alibaba",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": "{}",
                    "reasoning_details": [
                        {"type": "reasoning.text", "text": "private reasoning not for log"}
                    ],
                },
            }
        ],
    }
    g = ModelGateway(
        settings(tmp_path), transport=httpx.MockTransport(lambda r: httpx.Response(200, json=raw))
    )

    async def run():
        try:
            await g.json_request("critic", {})
        finally:
            await g.aclose()

    asyncio.run(run())
    assert g.calls[0]["thinking_mismatch"]
    assert "private reasoning" not in json.dumps(g.calls)


def test_provider_outage_does_not_change_route(tmp_path):
    routes = []

    def response(request):
        routes.append(json.loads(request.content)["provider"])
        return httpx.Response(503, json={"error": {"code": "provider_unavailable"}})

    g = ModelGateway(settings(tmp_path), transport=httpx.MockTransport(response))

    async def run():
        try:
            with pytest.raises(httpx.HTTPStatusError):
                await g.json_request("critic", {}, timeout=2)
        finally:
            await g.aclose()

    asyncio.run(run())
    assert len(routes) == 2 and all(
        r == routes[0] and r["only"] == ["alibaba"] and not r["allow_fallbacks"] for r in routes
    )


def test_separate_credential_selection(tmp_path, monkeypatch):
    from studio import config

    for key in list(config.os.environ):
        if key.startswith(("STUDIO_", "LLM_", "OPENROUTER_")):
            monkeypatch.delenv(key)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / ".env").write_text(
        "STUDIO_MODEL_BASE_URL=https://openrouter.ai/api/v1\nLLM_API_KEY=old-key\nOPENROUTER_API_KEY=new-key\n"
    )
    s = Settings.from_env()
    assert s.api_key == "new-key" and "new-key" not in repr(s)
    monkeypatch.setenv("STUDIO_MODEL_BASE_URL", "https://legacy.test/v1")
    assert Settings.from_env().api_key == "old-key"
    monkeypatch.setenv("STUDIO_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    assert Settings.from_env().api_key == ""


def test_no_key_or_route_fails_before_request(tmp_path):
    with pytest.raises(ModelPolicyError, match="OPENROUTER_API_KEY"):
        ModelGateway(replace(settings(tmp_path), api_key=""))
    with pytest.raises(ModelPolicyError, match="провайдеров"):
        ModelGateway(replace(settings(tmp_path), openrouter_providers=()))


def test_provider_selection_invalidates_all_model_caches(tmp_path):
    from studio.providers.induction import validated_request
    from studio.checks import review_cache
    from studio.templates.template_cache import TemplateCache

    s = settings(tmp_path)

    class G:
        def __init__(self, s):
            self.settings = s
            self.calls = []
            self.requests = 0

        async def json_request(self, *a, **kw):
            self.requests += 1
            return {"ok": True}

    async def call(g):
        return await validated_request(g, "editorial", {}, {}, lambda x: x, timeout=1)

    first = G(s)
    asyncio.run(call(first))
    assert first.requests == 1
    same = G(s)
    asyncio.run(call(same))
    assert same.requests == 0
    changed = G(replace(s, openrouter_providers=("other",)))
    asyncio.run(call(changed))
    assert changed.requests == 1
    assert review_cache.identity(first, "critic", {}) != review_cache.identity(
        changed, "critic", {}
    )
    assert TemplateCache(s).location("sha") != TemplateCache(changed.settings).location("sha")


def test_openrouter_json_mode_still_supplies_schema_for_server_validation(tmp_path):
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }

    def response(request):
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert json.dumps(schema, ensure_ascii=False) in body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "provider": "Alibaba",
                "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
            },
        )

    g = ModelGateway(
        replace(settings(tmp_path), structured_output=False),
        transport=httpx.MockTransport(response),
    )

    async def run():
        try:
            assert await g.json_request("critic", {}, schema=schema) == {"ok": True}
        finally:
            await g.aclose()

    asyncio.run(run())
    assert g.calls[0]["response_format"] == "json_object"
