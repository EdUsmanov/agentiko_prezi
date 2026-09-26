"""Optional OpenAI-compatible VL check for content embedded in smooth raster canvases."""

from __future__ import annotations

import base64
import hashlib
import json
import posixpath
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

import httpx
from PIL import Image, ImageOps

from .package import active_slide_parts
from .raster_regions import large_white_content_surface, smooth_light_canvas
from .resolution import inheritance_parts

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

PROMPT = (
    "Это ИСХОДНАЯ полноэкранная растровая подложка шаблона PowerPoint. "
    "Найди все видимые содержательные элементы, которые нельзя повторять как фон "
    "новой презентации: карточки и контейнеры текста, номера этапов, линии "
    "сетки, кнопки интерфейса, предметные пиктограммы, диаграммы и подписи. "
    "Сохрани однотонную и градиентную подложку, абстрактные фирменные орнаменты, "
    "логотипы и надписи бренда. Верни минимальное число прямоугольных областей, "
    "покрывающих целиком каждую группу удаляемого содержимого, включая тени. "
    "Координаты строго 0–1000 относительно размеров картинки: ни одно число "
    "не может быть больше 1000, не возвращай пиксельные координаты. Если изображение только "
    "декоративное, regions пуст. Никаких пояснений вне JSON."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "regions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "x1": {"type": "integer"},
                    "y1": {"type": "integer"},
                    "x2": {"type": "integer"},
                    "y2": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["x1", "y1", "x2", "y2", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["regions"],
    "additionalProperties": False,
}
REVIEW_VERSION = b"dual-enhanced-bgfill-pixel-normalization-2026-09-25"


def _enhanced_png(payload: bytes) -> bytes:
    with Image.open(BytesIO(payload)) as source:
        image = source.convert("RGB")
    image.thumbnail((1600, 1600))
    image = ImageOps.autocontrast(image, cutoff=1)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _normalized_regions(regions: object, payload: bytes) -> list[dict]:
    """Accept normalized boxes and recover pixel boxes from occasional VL replies."""
    if not isinstance(regions, list):
        raise ValueError("VL response has no region list")
    if any(
        not isinstance(region, dict)
        or not isinstance(region.get("reason"), str)
        or not all(
            isinstance(region.get(key), int) and not isinstance(region[key], bool)
            for key in ("x1", "y1", "x2", "y2")
        )
        or region["x1"] < 0
        or region["y1"] < 0
        or region["x1"] >= region["x2"]
        or region["y1"] >= region["y2"]
        for region in regions
    ):
        raise ValueError("VL response contains invalid boxes")
    if not regions:
        return []
    max_x = max(region["x2"] for region in regions)
    max_y = max(region["y2"] for region in regions)
    if max_x <= 1000 and max_y <= 1000:
        return regions
    with Image.open(BytesIO(payload)) as source:
        width, height = source.size
    if max_x > width * 1.3 or max_y > height * 1.3:
        raise ValueError("VL pixel boxes exceed plausible image bounds")
    scale_x = max(width, max_x)
    scale_y = max(height, max_y)
    normalized = []
    for region in regions:
        box = {
            key: round(region[key] * 1000 / (scale_x if key.startswith("x") else scale_y))
            for key in ("x1", "y1", "x2", "y2")
        }
        if box["x1"] >= box["x2"] or box["y1"] >= box["y2"]:
            raise ValueError("VL pixel box collapsed during normalization")
        normalized.append({**box, "reason": region["reason"]})
    return normalized


def _review(
    payload: bytes, endpoint: str, secret: str, model: str, enhanced: bool = False
) -> list[dict]:
    mime = "image/jpeg" if payload.startswith(b"\xff\xd8") else "image/png"
    content = [
        {"type": "text", "text": PROMPT},
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime};base64," + base64.b64encode(payload).decode(),
                "detail": "high",
            },
        },
    ]
    if enhanced:
        content[0]["text"] += (
            " Вторая картинка — та же подложка с усиленным контрастом, "
            "чтобы увидеть бледные границы карточек. Координаты дай по первой картинке."
        )
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,"
                    + base64.b64encode(_enhanced_png(payload)).decode(),
                    "detail": "high",
                },
            }
        )
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 5000,
        "messages": [
            {
                "role": "user",
                "content": content,
            }
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "background_regions", "strict": True, "schema": SCHEMA},
        },
    }
    if model.endswith("-noreason"):
        body["reasoning_effort"] = "none"
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with httpx.Client(timeout=180) as client:
                response = client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {secret}"},
                    json=body,
                )
                response.raise_for_status()
                answer = response.json()
            content = answer["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("VL response has no final JSON content")
            return _normalized_regions(json.loads(content)["regions"], payload)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2**attempt)
    raise RuntimeError(f"VL raster review failed: {type(last_error).__name__}") from last_error


def _background_fill_assets(package: zipfile.ZipFile) -> set[str]:
    """Find raster p:bg fills on active slides, layouts and masters.

    Background fills are not shape-tree objects, so the ordinary object model
    cannot name their media. A subject image baked into one would otherwise
    bypass the VL review entirely.
    """
    assets: set[str] = set()
    names = set(package.namelist())
    if "ppt/presentation.xml" not in names:
        return assets
    drawing_parts = {
        part for slide in active_slide_parts(package) for part in inheritance_parts(package, slide)
    }
    for part in drawing_parts:
        root = ET.fromstring(package.read(part))
        embeds = {blip.get(f"{{{R}}}embed") for blip in root.findall(f".//{{{P}}}bg//{{{A}}}blip")}
        embeds.discard(None)
        if not embeds:
            continue
        path = PurePosixPath(part)
        relations_part = str(path.parent / "_rels" / f"{path.name}.rels")
        if relations_part not in names:
            continue
        for relation in ET.fromstring(package.read(relations_part)):
            if (
                relation.get("Id") not in embeds
                or not relation.get("Type", "").endswith("/image")
                or relation.get("TargetMode") == "External"
            ):
                continue
            target = relation.get("Target", "")
            asset = (
                target.lstrip("/")
                if target.startswith("/")
                else posixpath.normpath(posixpath.join(str(path.parent), target))
            )
            if asset in names and asset.lower().endswith((".png", ".jpg", ".jpeg")):
                assets.add(asset)
    return assets


def review_raster_backgrounds(
    reference: bytes,
    model: dict,
    endpoint: str,
    secret: str,
    vl_model: str,
    cache_dir: Path | None = None,
) -> None:
    """Review bright canvas assets in slide and inherited OOXML parts."""
    assets = {
        item["asset"]
        for rows in model["parts"].values()
        for item in rows
        if item["action"] == "keep"
        and item["role"] == "background"
        and item["asset"].lower().endswith((".png", ".jpg", ".jpeg"))
        and item["box"][2] * item["box"][3] > 0.6
    }
    review = model.setdefault("rasterReview", {"model": vl_model, "assets": []})
    with zipfile.ZipFile(BytesIO(reference)) as package:
        assets.update(_background_fill_assets(package))
        for asset in sorted(assets):
            payload = package.read(asset)
            if not smooth_light_canvas(payload):
                continue
            cache_path = None
            if cache_dir is not None:
                key = hashlib.sha256(
                    REVIEW_VERSION + vl_model.encode() + b"\0" + payload
                ).hexdigest()
                cache_path = cache_dir / f"{key}.json"
            cached = (
                json.loads(cache_path.read_text()) if cache_path and cache_path.exists() else None
            )
            if cached is not None and cached.get("enhancedReviewed"):
                regions = cached["regions"]
            else:
                # The first view can miss pale empty cards while finding a
                # prominent icon. Ask again with contrast enhancement and
                # exclude the union when fitting the clean canvas.
                regions = _review(payload, endpoint, secret, vl_model)
                regions.extend(_review(payload, endpoint, secret, vl_model, enhanced=True))
            if not regions:
                regions = large_white_content_surface(payload)
            if cache_path is not None and cached is None:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = cache_path.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps({"regions": regions, "enhancedReviewed": True}, ensure_ascii=False)
                )
                temporary.replace(cache_path)
            review["assets"].append({"asset": asset, "regions": len(regions)})
            print(f"VL review {asset}: {len(regions)} region(s)", file=sys.stderr, flush=True)
            if regions:
                model.setdefault("rasterRegions", {})[asset] = regions

