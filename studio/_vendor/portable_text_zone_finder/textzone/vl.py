"""Optional no-reason VL obstacle detection, isolated from geometry code."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

MODEL = "qwen3.8-27b-noreason"
PROMPTS = {
    "base": (
        "This full slide background has a yellow 8x8 grid. "
        "List cells touched by slide artwork, logo, icons, patterns, photos or decorative shapes. "
        "Ignore the grid and outside labels. Plain color and smooth gradients are empty. "
        "Answer cell IDs A1-H8 only, comma separated. No explanation."
    ),
    "strict": (
        "This full slide background has a yellow 8x8 grid. "
        "Return all grid cells that are NOT completely clear for text. "
        "Include a cell if even a small piece of a logo, picture, pattern, stripe, "
        "decoration, line art, or sharp color boundary touches it. "
        "Ignore the yellow grid and labels. Smooth gradient and solid fill are clear. "
        "Output only comma-separated IDs A1-H8. No explanation."
    ),
}


def image_key(contents: bytes) -> str:
    """Stable cache key shared by duplicate backgrounds across slides."""
    return hashlib.sha256(contents).hexdigest()[:20]


def _font() -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", 20)
    except OSError:
        return ImageFont.load_default()


def _grid_image(source: Image.Image) -> str:
    """Give VL explicit 8×8 coordinates; labels stay outside the slide."""
    width = 960
    height = round(source.height / source.width * width)
    slide = source.convert("RGB").resize((width, height))
    canvas = Image.new("RGB", (1004, height + 36), "#101010")
    canvas.paste(slide, (44, 36))
    draw = ImageDraw.Draw(canvas)
    font = _font()
    for col in range(8):
        x = 44 + col * 120
        draw.text((x + 51, 7), chr(65 + col), font=font, fill="white")
        if col:
            draw.line((x, 36, x, 35 + height), fill="#ffee00", width=2)
    for row in range(8):
        y = 36 + round(row * height / 8)
        draw.text((12, y + max(0, round(height / 16) - 10)), str(row + 1), font=font, fill="white")
        if row:
            draw.line((44, y, 1003, y), fill="#ffee00", width=2)
    buffer = io.BytesIO()
    canvas.save(buffer, "JPEG", quality=60, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _cached_cells(path: Path) -> set[str] | None:
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    if record.get("finish_reason") != "stop":
        return None
    cells = record.get("cells")
    if not isinstance(cells, list):
        return None
    return set(cells)


def _query(client: httpx.Client, grid: str, variant: str) -> dict:
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPTS[variant]},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + grid}},
                ],
            }
        ],
        "temperature": 0,
        "reasoning_effort": "none",
        "max_tokens": 1400,
    }
    for attempt in range(3):
        try:
            response = client.post(os.environ["LLM_CHAT_COMPLETIONS_URL"], json=payload)
            response.raise_for_status()
            result = response.json()
            choice = result["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ValueError("VL response was incomplete")
            content = choice["message"].get("content") or ""
            return {
                "variant": variant,
                "cells": sorted(set(re.findall(r"\b[A-H][1-8]\b", content.upper()))),
                "content": content,
                "finish_reason": "stop",
                "reasoning_tokens": result.get("usage", {})
                .get("completion_tokens_details", {})
                .get("reasoning_tokens"),
            }
        except (httpx.HTTPError, KeyError, ValueError) as error:
            if attempt == 2:
                raise RuntimeError(f"VL request failed: {type(error).__name__}") from error
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def load_or_query_cells(
    contents: bytes,
    source: Image.Image,
    cache_dir: Path,
    live: bool,
) -> set[str] | None:
    """Return the union of two prompts, or None if offline cache is incomplete."""
    key = image_key(contents)
    found = {variant: _cached_cells(cache_dir / f"{key}-{variant}.json") for variant in PROMPTS}
    missing = [variant for variant, cells in found.items() if cells is None]
    if missing and not live:
        return None
    if missing:
        endpoint = os.getenv("LLM_CHAT_COMPLETIONS_URL")
        api_key = os.getenv("LLM_API_KEY")
        if not endpoint or not api_key:
            raise ValueError("--vl requires LLM_CHAT_COMPLETIONS_URL and LLM_API_KEY")
        grid = _grid_image(source)
        cache_dir.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=90, headers={"Authorization": f"Bearer {api_key}"}) as client:
            for variant in missing:
                record = _query(client, grid, variant)
                record["key"] = key
                (cache_dir / f"{key}-{variant}.json").write_text(
                    json.dumps(record, ensure_ascii=False, indent=2)
                )
                found[variant] = set(record["cells"])
    return set().union(*(cells for cells in found.values() if cells is not None))

