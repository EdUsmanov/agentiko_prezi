"""Golden HTTP responses exercise the actual gateway, validators and private cache."""

import asyncio
import base64
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path

import httpx
from PIL import Image
import pytest

from studio.config import Settings
from studio.gateway import ModelGateway, ModelResponseTruncated
from studio.induction import InductionFailure, validated_request
from test_support.replay import Replay, request_contract

FIXTURES = Path(__file__).parent / "fixtures/model_responses"
SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}
PAYLOAD = {"text": "Пилот: 12 недель; контроль: 40 заявок."}


def gateway(tmp_path, name):
    replay = Replay(FIXTURES / (name + ".json"))
    settings = Settings(
        data_dir=tmp_path,
        mode="api",
        base_url="https://replay.invalid/v1",
        model_id="fixture-27b",
        parameters_b=27,
        open_weights=True,
        license="Apache-2.0",
        structured_output=False,
    )
    return ModelGateway(settings, transport=replay.transport()), replay


def valid(value):
    if set(value) != {"ok"} or type(value["ok"]) is not bool:
        raise ValueError("Expected a single boolean ok")
    return value


def test_replay_rate_limit_retries_the_same_request(tmp_path):
    model, replay = gateway(tmp_path, "gateway-rate-limit")

    async def run():
        try:
            assert await model.json_request("author", PAYLOAD, schema=SCHEMA) == {"ok": True}
        finally:
            await model.aclose()

    asyncio.run(run())
    replay.assert_consumed()
    assert model.calls[0]["attempts"] == 2
    assert [call["status"] for call in replay.calls] == [429, 200]


@pytest.mark.parametrize(
    "case,error",
    [
        ("malformed", ValueError),
        ("truncated", ModelResponseTruncated),
        ("denied", httpx.HTTPStatusError),
    ],
)
def test_invalid_provider_response_never_becomes_a_success(tmp_path, case, error):
    model, replay = gateway(tmp_path, "gateway-" + case)

    async def run():
        try:
            with pytest.raises(error):
                await model.json_request("author", PAYLOAD, schema=SCHEMA)
        finally:
            await model.aclose()

    asyncio.run(run())
    replay.assert_consumed()
    assert model.calls[0]["status"] == "failed"
    assert model.calls[0]["attempts"] == 1
    assert not list(tmp_path.rglob("*.json"))


def test_schema_repair_is_bounded_and_only_validated_answer_is_cached(tmp_path):
    model, replay = gateway(tmp_path, "gateway-repair")

    async def run():
        try:
            for _ in range(2):
                assert await validated_request(
                    model, "author", PAYLOAD, SCHEMA, valid, timeout=3
                ) == {"ok": True}
        finally:
            await model.aclose()

    asyncio.run(run())
    replay.assert_consumed()
    assert len(replay.calls) == 2
    assert model.calls[-1]["cache_hit"] is True
    caches = list((tmp_path / "analysis-cache").glob("*.json"))
    assert len(caches) == 1
    assert json.loads(caches[0].read_text()) == {"ok": True}


def test_invalid_answers_exhaust_repair_without_persisting_cache(tmp_path):
    model, replay = gateway(tmp_path, "gateway-invalid")

    async def run():
        try:
            with pytest.raises(InductionFailure) as failure:
                await validated_request(model, "author", PAYLOAD, SCHEMA, valid, timeout=3)
            assert len(failure.value.attempt_errors) == 2
            assert failure.value.fatal is False
        finally:
            await model.aclose()

    asyncio.run(run())
    replay.assert_consumed()
    assert len(replay.calls) == 2
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("change", ["source", "prompt", "schema", "model"])
def test_changed_contract_cannot_silently_use_a_golden_answer(tmp_path, change):
    model, replay = gateway(tmp_path, "gateway-success")
    payload, schema, stage = deepcopy(PAYLOAD), deepcopy(SCHEMA), "author"
    if change == "source":
        payload["text"] += " Extra evidence."
    elif change == "prompt":
        stage = "critic"
    elif change == "schema":
        schema["description"] = "A different contract"
    else:
        from dataclasses import replace

        model.settings = replace(model.settings, model_id="other-27b")

    async def run():
        try:
            with pytest.raises(httpx.HTTPStatusError):
                await model.json_request(stage, payload, schema=schema)
        finally:
            await model.aclose()

    asyncio.run(run())
    assert len(replay.mismatches) == 1 and not replay.calls
    with pytest.raises(AssertionError, match="Unexpected replay"):
        replay.assert_consumed()


def test_unused_and_exhausted_cassette_are_failures(tmp_path):
    model, replay = gateway(tmp_path, "gateway-success")
    with pytest.raises(AssertionError, match="Unconsumed"):
        replay.assert_consumed()

    async def run():
        try:
            assert await model.json_request("author", PAYLOAD, schema=SCHEMA) == {"ok": True}
            replay.assert_consumed()
            with pytest.raises(httpx.HTTPStatusError):
                await model.json_request("author", PAYLOAD, schema=SCHEMA)
        finally:
            await model.aclose()

    asyncio.run(run())
    with pytest.raises(AssertionError, match="Unexpected replay"):
        replay.assert_consumed()


def test_image_policy_is_explicit_and_pixels_are_checked_by_default():
    def body(color):
        stream = BytesIO()
        Image.new("RGB", (20, 10), color).save(stream, format="PNG")
        return {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,"
                                + base64.b64encode(stream.getvalue()).decode()
                            },
                        }
                    ],
                }
            ]
        }

    red, blue = body("red"), body("blue")
    assert request_contract(red) != request_contract(blue)
    assert request_contract(red, image_matching="dimensions") == request_contract(
        blue, image_matching="dimensions"
    )
    with pytest.raises(ValueError, match="policy"):
        request_contract(red, image_matching="ignore")


def test_replay_transport_refuses_another_host():
    replay = Replay(FIXTURES / "gateway-success.json")
    with httpx.Client(transport=replay.transport()) as client:
        with pytest.raises(AssertionError, match="endpoint"):
            client.post("https://other.invalid/v1/chat/completions", json={})
