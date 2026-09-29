"""Peer VL grid recognition through Studio's bounded gateway and versioned cache."""

import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Annotated
from PIL import Image
from pydantic import Field, StringConstraints, ValidationError
from studio.models import StrictModel, Box
from studio.config import ROOT
from studio._vendor.portable_text_zone_finder.textzone.vl import _grid_image, PROMPTS
from studio._vendor.portable_text_zone_finder.textzone import analyze_image
from studio._vendor.portable_text_zone_finder.textzone.core import ZoneConfig
from studio.cache_version import atomic_json


class GridCells(StrictModel):
    cells: list[Annotated[str, StringConstraints(pattern=r"^[A-H][1-8]$")]] = Field(max_length=64)


async def recognize_cells(image, gateway, directory):
    # Reuse the peer's exact coordinate grid; the gateway accepts PNG only.
    with Image.open(BytesIO(base64.b64decode(_grid_image(image)))) as grid:
        output = BytesIO()
        grid.save(output, "PNG")
        data = output.getvalue()
    version = {
        "model": gateway.settings.model_id,
        "endpoint": gateway.settings.base_url,
        "prompts": PROMPTS,
        "adapter": 1,
        "system": (ROOT / "prompts/text_zone.md").read_text(),
    }
    from studio.checks.review_cache import identity

    key = identity(
        gateway, "text_zone", {"image": hashlib.sha256(data).hexdigest(), "version": version}
    )
    cache_root = Path(getattr(gateway.settings, "data_dir", directory))
    cells = set()
    for variant, instruction in PROMPTS.items():
        target = cache_root / "text-zone-cache" / f"{key}-{variant}.json"
        record = None
        if target.is_file():
            try:
                record = GridCells.model_validate_json(target.read_text())
            except (ValueError, OSError):
                pass
        if record is None:
            for attempt in range(2):
                raw = await gateway.json_request(
                    "text_zone",
                    {"variant": variant, "task": instruction},
                    timeout=90,
                    schema=GridCells.model_json_schema(),
                    images=[data],
                )
                if isinstance(raw, list) and len(raw) == 1 and isinstance(raw[0], dict):
                    raw = raw[0]
                try:
                    record = GridCells.model_validate(raw)
                    break
                except ValidationError:
                    if attempt:
                        raise
            atomic_json(target, record.model_dump())
        cells.update(record.cells)
    return cells


def check_fields(pattern, profile, image, metadata, cells):
    """Check each authored field separately; never merge columns into one free rectangle."""
    checks = []
    sx, sy = image.width / profile.width, image.height / profile.height
    for role, zones in (("title", [pattern.title_zone]), ("body", pattern.body_zones)):
        for index, zone in enumerate(zones):
            if zone is None:
                continue
            bounds = [
                max(0, round(zone.x * sx)),
                max(0, round(zone.y * sy)),
                min(image.width, round((zone.x + zone.w) * sx)),
                min(image.height, round((zone.y + zone.h) * sy)),
            ]
            x, y, right, bottom = bounds
            w, h = right - x, bottom - y
            row = {"role": role, "index": index, "status": "unknown", "before": zone.model_dump()}
            if min(w, h) < 16:
                row["reason"] = "field_too_small"
                checks.append(row)
                continue
            local_regions = []
            for region in metadata.get("protectedRegions", []):
                left = region["x"] * image.width / 1280 - x
                top = region["y"] * image.width / 1280 - y
                local_regions.append(
                    {
                        **region,
                        "x": left,
                        "y": top,
                        "width": region["width"] * image.width / 1280,
                        "height": region["height"] * image.width / 1280,
                    }
                )
            local_cells = None
            if cells is not None:
                local_cells = set()
                for col in range(8):
                    for line in range(8):
                        a, b = x + col * w / 8, y + line * h / 8
                        c, d = a + w / 8, b + h / 8
                        if any(
                            min(c, (ord(cell[0]) - 64) * image.width / 8)
                            > max(a, (ord(cell[0]) - 65) * image.width / 8)
                            and min(d, int(cell[1]) * image.height / 8)
                            > max(b, (int(cell[1]) - 1) * image.height / 8)
                            for cell in cells
                        ):
                            local_cells.add(chr(65 + col) + str(line + 1))
            config = ZoneConfig(
                minimum_width=max(16, round(w * 0.55)),
                minimum_height=max(16, round(h * 0.3)),
                fallback_minimum_width=max(16, round(w * 0.55)),
                edge_margin=0,
                object_clearance=24,
            )
            result = analyze_image(
                image.crop(bounds),
                {"protectedRegions": local_regions, "objects": []},
                local_cells,
                config,
            )
            row.update(
                reason=result["reason"],
                recognition_mode=result["recognition_mode"],
                minimum_contrast=result["minimum_contrast"],
            )
            if result["box"]:
                left, top, r, b = result["box"]
                safe = Box(x=max(zone.x, (x + left) / sx), y=max(zone.y, (y + top) / sy), w=0, h=0)
                safe.w = min(zone.x + zone.w, (x + r) / sx) - safe.x
                safe.h = min(zone.y + zone.h, (y + b) / sy) - safe.y
                row.update(
                    status="safe"
                    if safe.w >= zone.w * 0.97 and safe.h >= zone.h * 0.97
                    else "constrained",
                    after=safe.model_dump(),
                )
                if row["status"] == "constrained":
                    if role == "title":
                        pattern.title_zone = safe
                    else:
                        pattern.body_zones[index] = safe
                    for field in pattern.fields:
                        if field["role"] == role and field["index"] == index:
                            field["box"] = safe.model_dump()
            checks.append(row)
    pattern.text_zones = [pattern.title_zone] + pattern.body_zones
    pattern.safe_text_zone["field_checks"] = checks
    return checks


async def review_text_zones(profile, directory, gateway, progress=None):
    from studio.templates.portable_templates import inspect_text_zone, zone_metadata

    report = {"status": "completed", "patterns": [], "model": gateway.settings.model_id}
    path = Path(directory) / "background-model.json"
    if not path.is_file():
        return {**report, "status": "not_run", "reason": "background_model_missing"}
    model = json.loads(path.read_text())
    live = gateway.settings.mode == "api" and getattr(gateway.settings, "visual_review", False)
    unavailable = False
    for pattern in profile.patterns:
        if not pattern.background_image or not Path(pattern.background_image).is_file():
            continue
        with Image.open(pattern.background_image) as source:
            image = source.convert("RGB").resize(
                (1280, round(1280 * profile.height / profile.width))
            )
        metadata = zone_metadata(pattern, profile, model)
        initial = analyze_image(image, metadata)
        cells = None
        status = "pixels_sufficient"
        if initial["recognition_mode"] == "complex_surface_requires_vl":
            status = "not_run"
            if live and not unavailable:
                if progress:
                    progress("Проверяем сложный фон визуальной моделью: " + pattern.id)
                try:
                    cells = await recognize_cells(image, gateway, directory)
                    status = "completed"
                except Exception as exc:
                    status = "failed"
                    report["error_type"] = type(exc).__name__
                    unavailable = True  # No cascade across the remaining complex slides.
        inspect_text_zone(image, pattern, profile, model, vl_cells=cells)
        checks = check_fields(pattern, profile, image, metadata, cells)
        if (
            status == "pixels_sufficient"
            and live
            and not unavailable
            and any(check["status"] == "unknown" for check in checks)
        ):
            if progress:
                progress("Проверяем спорное текстовое поле визуальной моделью: " + pattern.id)
            try:
                cells = await recognize_cells(image, gateway, directory)
                status = "completed"
                inspect_text_zone(image, pattern, profile, model, vl_cells=cells)
                checks = check_fields(pattern, profile, image, metadata, cells)
            except Exception as exc:
                status = "failed"
                report["error_type"] = type(exc).__name__
                unavailable = True
        pattern.safe_text_zone["application"] = (
            "per-field constraints; unknown requires review; authored fields retained"
        )
        report["patterns"].append(
            {"pattern_id": pattern.id, "vl_status": status, "field_checks": checks}
        )
        if status in ("failed", "not_run") or any(c["status"] == "unknown" for c in checks):
            report["status"] = "needs_review"
    atomic_json(Path(directory) / "text-zone-review.json", report)
    atomic_json(
        Path(directory) / "text-zones.json", {p.id: p.safe_text_zone for p in profile.patterns}
    )
    return report
