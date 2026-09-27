import asyncio
from types import SimpleNamespace
import pytest
from PIL import Image
from studio.text_zone_review import recognize_cells, check_fields
from studio.models import Box, Pattern


def test_two_grid_observations_are_unioned_cached_and_model_specific(tmp_path):
    calls = []

    async def request(stage, payload, **kwargs):
        calls.append((stage, payload))
        assert kwargs["images"][0].startswith(b"\x89PNG")
        return {"cells": ["A1"] if payload["variant"] == "base" else ["H8"]}

    gateway = SimpleNamespace(
        settings=SimpleNamespace(model_id="qwen-test", base_url="https://example.test"),
        json_request=request,
    )
    image = Image.new("RGB", (1280, 720), "white")
    assert asyncio.run(recognize_cells(image, gateway, tmp_path)) == {"A1", "H8"}
    assert len(calls) == 2
    assert asyncio.run(recognize_cells(image, gateway, tmp_path)) == {"A1", "H8"}
    assert len(calls) == 2
    gateway.settings.model_id = "other"
    asyncio.run(recognize_cells(image, gateway, tmp_path))
    assert len(calls) == 4


def test_malformed_grid_is_not_cached_as_clear(tmp_path):
    async def request(*args, **kwargs):
        return {"cells": ["A0"]}

    gateway = SimpleNamespace(
        settings=SimpleNamespace(model_id="test", base_url="https://example.test"),
        json_request=request,
    )
    with pytest.raises(ValueError):
        asyncio.run(recognize_cells(Image.new("RGB", (1280, 720), "white"), gateway, tmp_path))
    assert not list(tmp_path.rglob("*.json"))


def test_field_geometry_keeps_cards_separate_and_reports_unknown():
    image = Image.new("RGB", (1280, 720), "white")
    zones = [Box(x=50, y=200, w=400, h=300), Box(x=650, y=200, w=400, h=300)]
    p = Pattern(
        id="cards",
        source_slide=1,
        source_layout="cards",
        role="columns",
        title_zone=Box(x=50, y=30, w=1000, h=80),
        body_zones=zones,
        text_zones=zones,
        fields=[],
    )
    checks = check_fields(p, SimpleNamespace(width=1280, height=720), image, {}, None)
    assert all(c["status"] == "safe" for c in checks)
    assert p.body_zones == zones
    checks = check_fields(
        p,
        SimpleNamespace(width=1280, height=720),
        image,
        {"protectedRegions": [{"x": 650, "y": 200, "width": 400, "height": 300}]},
        None,
    )
    assert next(c for c in checks if c["role"] == "body" and c["index"] == 1)["status"] == "unknown"


def test_vl_failure_is_explicit_and_stops_request_cascade(tmp_path):
    import json
    import numpy as np
    from studio.text_zone_review import review_text_zones

    pixels = np.indices((720, 1280)).sum(axis=0) // 8 % 2 * 180
    path = tmp_path / "complex.png"
    Image.fromarray(pixels.astype("uint8")).convert("RGB").save(path)
    box = Box(x=100, y=100, w=700, h=300)
    patterns = [
        Pattern(
            id=f"p{i}",
            source_slide=1,
            source_layout="complex",
            role="statement",
            title_zone=box,
            body_zones=[box],
            text_zones=[box],
            background_image=str(path),
        )
        for i in range(2)
    ]
    profile = SimpleNamespace(patterns=patterns, width=1280, height=720)
    (tmp_path / "background-model.json").write_text(
        json.dumps({"slides": [{"objects": [], "protectedRegions": []}]})
    )
    calls = []

    async def request(*args, **kwargs):
        calls.append(1)
        raise TimeoutError()

    gateway = SimpleNamespace(
        settings=SimpleNamespace(
            mode="api", visual_review=True, model_id="test", base_url="https://example.test"
        ),
        json_request=request,
    )
    report = asyncio.run(review_text_zones(profile, tmp_path, gateway))
    assert len(calls) == 1
    assert report["status"] == "needs_review"
    assert [p["vl_status"] for p in report["patterns"]] == ["failed", "not_run"]
    assert all(c["status"] == "unknown" for p in report["patterns"] for c in p["field_checks"])
