import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fontTools.ttLib import TTFont
from pptx import Presentation

from studio import font_manifest
from studio.analysis import analyze_meaning
from studio.config import ROOT


def result(payload):
    return {
        "patterns": [
            {
                "pattern_id": p["id"],
                "purpose": "content",
                "roles": ["evidence"],
                "density": "medium",
                "reusable": True,
            }
            for p in payload["patterns"]
        ]
    }


def inventory(count):
    return {
        "width": 960,
        "height": 540,
        "patterns": [{"id": f"p{i}", "source_slide": i + 1} for i in range(count)],
    }


def test_installed_restricted_face_is_local_only_without_changing_font(
    template, tmp_path, monkeypatch
):
    font_path = tmp_path / "system-font.ttf"
    with TTFont(ROOT / "fonts/Play-Regular.ttf") as font:
        font["OS/2"].fsType = 8
        font.save(font_path)
    before = font_path.read_bytes()
    monkeypatch.setattr(font_manifest, "resolve_font", lambda name: str(font_path))
    report = font_manifest.build_font_manifest(
        Presentation(template), template, tmp_path / "report"
    )
    assert not report["unresolved"]
    assert all(not a["redistributable"] and a["fs_type"] == 8 for a in report["assets"])
    assert font_path.read_bytes() == before
    assert report["limitations"][-1] == "Local-only font assets are not embedded into PPTX/HTML/PDF"


def test_downloaded_restricted_face_is_still_rejected(template, tmp_path, monkeypatch):
    from studio import open_fonts

    font_path = tmp_path / "download.ttf"
    with TTFont(ROOT / "fonts/Play-Regular.ttf") as font:
        font["OS/2"].fsType = 8
        font.save(font_path)
    monkeypatch.setattr(font_manifest, "resolve_font", lambda name: "")
    monkeypatch.setattr(open_fonts, "download_face", lambda name: (str(font_path), None))
    report = font_manifest.build_font_manifest(
        Presentation(template), template, tmp_path / "report", allow_download=True
    )
    assert report["unresolved"]
    assert not report["assets"]


def test_visual_batches_are_serial_and_have_one_distinct_source_image():
    class Gateway:
        settings = SimpleNamespace(mode="api")

        def __init__(self):
            self.active = 0
            self.maximum = 0
            self.seen = []

        async def json_request(self, stage, payload, **kwargs):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.seen.append((payload["image_order"], len(kwargs["images"])))
            await asyncio.sleep(0)
            self.active -= 1
            return result(payload)

    gateway = Gateway()
    phases = []
    report = asyncio.run(
        analyze_meaning(
            inventory(5), gateway, images={n: b"image" for n in range(1, 6)}, progress=phases.append
        )
    )
    assert report["status"] == "completed"
    assert gateway.maximum == 1
    assert gateway.seen == [([n], 1) for n in range(1, 6)]
    assert phases[-1] == "Проверено макетов 5 из 5"


def test_classifier_response_budget_and_truncation_metadata(tmp_path):
    import json
    from dataclasses import replace
    from tests.test_gateway import settings
    from studio.gateway import ModelGateway

    def handler(request):
        body = json.loads(request.content)
        assert body["max_tokens"] >= 4096
        assert body["chat_template_kwargs"]["enable_thinking"] is False
        assert "thinking_token_budget" not in body["chat_template_kwargs"]
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "length", "message": {"content": ""}}],
                "usage": {"completion_tokens": 4608},
            },
        )

    gateway = ModelGateway(
        replace(settings(tmp_path), thinking=True), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ValueError):
        asyncio.run(gateway.json_request("template_analyst", {"patterns": [{"id": "p1"}]}))
    assert gateway.calls[-1]["finish_reason"] == "length"
    assert gateway.calls[-1]["usage"] == {"completion_tokens": 4608}


def test_cached_blocks_do_not_reset_provider_outage_streak(tmp_path):
    class Gateway:
        settings = SimpleNamespace(mode="api", data_dir=tmp_path)

        def __init__(self, fail):
            self.fail = fail
            self.requests = []
            self.calls = []

        async def json_request(self, stage, payload, **kwargs):
            ids = [p["id"] for p in payload["patterns"]]
            self.requests.extend(ids)
            if self.fail:
                raise TimeoutError()
            return result(payload)

    images = {n: b"image" for n in range(1, 5)}
    seed = inventory(4)
    seed["patterns"] = seed["patterns"][1::2]
    asyncio.run(analyze_meaning(seed, Gateway(False), images))
    failing = Gateway(True)
    report = asyncio.run(analyze_meaning(inventory(4), failing, images))
    assert report["status"] == "failed" and report["error_type"] == "ProviderUnavailable"
    assert failing.requests == ["p0", "p0", "p2", "p2"]


def test_provider_outage_stops_without_retrying_the_entire_deck():
    class Gateway:
        settings = SimpleNamespace(mode="api")
        requests = 0

        async def json_request(self, *args, **kwargs):
            self.requests += 1
            raise TimeoutError("PRIVATE provider details")

    gateway = Gateway()
    report = asyncio.run(
        analyze_meaning(inventory(30), gateway, images={n: b"image" for n in range(1, 31)})
    )
    assert gateway.requests == 4  # two attempts, two failed single-source blocks
    assert report["status"] == "failed" and report["error_type"] == "ProviderUnavailable"
    assert len(report["excluded_pattern_ids"]) == 30
    assert "PRIVATE" not in str(report)


def test_auth_failure_does_not_split_or_retry():
    class Gateway:
        settings = SimpleNamespace(mode="api")
        requests = 0

        async def json_request(self, *args, **kwargs):
            self.requests += 1
            response = httpx.Response(401, request=httpx.Request("POST", "https://example.invalid"))
            raise httpx.HTTPStatusError("PRIVATE", request=response.request, response=response)

    gateway = Gateway()
    report = asyncio.run(analyze_meaning(inventory(30), gateway))
    assert gateway.requests == 1
    assert report["status"] == "failed" and report["error_type"] == "HTTPStatusError"


def test_timed_out_large_batch_is_split_without_identical_retry():
    class Gateway:
        settings = SimpleNamespace(mode="api")

        def __init__(self):
            self.seen = []

        async def json_request(self, stage, payload, **kwargs):
            ids = [p["id"] for p in payload["patterns"]]
            self.seen.append(ids)
            if len(ids) > 1:
                raise TimeoutError()
            return result(payload)

    gateway = Gateway()
    report = asyncio.run(analyze_meaning(inventory(3), gateway))
    assert gateway.seen == [["p0", "p1", "p2"], ["p0"], ["p1"], ["p2"]]
    assert report["status"] == "completed"


def test_image_free_layout_batches_are_small():
    class Gateway:
        settings = SimpleNamespace(mode="api")

        def __init__(self):
            self.sizes = []

        async def json_request(self, stage, payload, **kwargs):
            self.sizes.append(len(payload["patterns"]))
            return result(payload)

    gateway = Gateway()
    report = asyncio.run(analyze_meaning(inventory(15), gateway))
    assert gateway.sizes == [4, 4, 4, 3]
    assert report["status"] == "completed"


def test_truncated_batch_splits_without_identical_retry():
    from studio.gateway import ModelResponseTruncated

    class Gateway:
        settings = SimpleNamespace(mode="api")

        def __init__(self):
            self.seen = []

        async def json_request(self, stage, payload, **kwargs):
            ids = [p["id"] for p in payload["patterns"]]
            self.seen.append(ids)
            if len(ids) > 1:
                raise ModelResponseTruncated("token limit")
            return result(payload)

    gateway = Gateway()
    report = asyncio.run(analyze_meaning(inventory(3), gateway))
    assert gateway.seen == [["p0", "p1", "p2"], ["p0"], ["p1"], ["p2"]]
    assert report["status"] == "completed"
