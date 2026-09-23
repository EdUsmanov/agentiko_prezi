import asyncio
import json
import httpx
import pytest
from studio.config import Settings
from studio.gateway import ModelGateway


def settings(tmp_path):
    return Settings(data_dir=tmp_path, mode="api", base_url="https://example.test/v1",
                    model_id="test-open-model", api_key="secret-test", open_weights=True,
                    parameters_b=27, license="Apache-2.0", thinking=False, structured_output=True)


def test_structured_json_and_data_boundary(tmp_path):
    schema={"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"],"additionalProperties":False}
    def handler(request):
        body=json.loads(request.content)
        assert str(request.url)=="https://example.test/v1/chat/completions"
        assert request.headers["Authorization"]=="Bearer secret-test"
        assert "secret-test" not in request.content.decode()
        assert body["chat_template_kwargs"]=={"enable_thinking":False}
        assert body["response_format"]["json_schema"]["schema"]==schema
        assert "tools" not in body
        assert "INJECTED" not in body["messages"][0]["content"]
        assert json.loads(body["messages"][1]["content"])["untrusted_input"]=={"text":"INJECTED"}
        return httpx.Response(200,json={"choices":[{"finish_reason":"stop","message":{"content":'{"ok":true}'}}],"usage":{"total_tokens":12}})
    gateway=ModelGateway(settings(tmp_path),transport=httpx.MockTransport(handler))
    assert asyncio.run(gateway.json_request("author",{"text":"INJECTED"},schema=schema))=={"ok":True}
    assert gateway.usage==[{"total_tokens":12}]


@pytest.mark.parametrize("reason",["length","tool_calls","content_filter",None])
def test_reject_incomplete_or_tool_response(tmp_path,reason):
    transport=httpx.MockTransport(lambda r:httpx.Response(200,json={"choices":[{"finish_reason":reason,"message":{"content":"{}"}}]}))
    with pytest.raises(ValueError):
        asyncio.run(ModelGateway(settings(tmp_path),transport=transport).json_request("author",{}))


def test_redirect_does_not_forward_credentials(tmp_path):
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(307,headers={"Location":"https://elsewhere.test/exfiltrate"})
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(ModelGateway(settings(tmp_path),transport=httpx.MockTransport(handler)).json_request("author",{}))
    assert calls==["https://example.test/v1/chat/completions"]
