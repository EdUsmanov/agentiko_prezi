"""Review content baked into smooth template backgrounds through ModelGateway."""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
import hashlib
import json

from PIL import Image, ImageOps
from pydantic import Field, model_validator

from studio.cache_version import atomic_json
from studio.config import ROOT
from studio.models import StrictModel
from studio.templates.portable_templates import template_reference
from studio.security import digest
from studio._vendor.portable_background_extractor.bgextract import inspect_template_backgrounds
from studio._vendor.portable_background_extractor.bgextract.raster_regions import (
    large_white_content_surface,
    smooth_light_canvas,
)
from studio._vendor.portable_background_extractor.bgextract.vl_regions import (
    PROMPT,
    _background_fill_assets,
)


class RasterRegion(StrictModel):
    x1: int = Field(strict=True, ge=0, le=1000)
    y1: int = Field(strict=True, ge=0, le=1000)
    x2: int = Field(strict=True, ge=0, le=1000)
    y2: int = Field(strict=True, ge=0, le=1000)
    reason: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def ordered(self):
        if self.x1 >= self.x2 or self.y1 >= self.y2:
            raise ValueError("VL вернула некорректную область растрового фона")
        return self


class RasterRegions(StrictModel):
    regions: list[RasterRegion] = Field(max_length=32)


def _png_view(payload: bytes, enhanced: bool) -> bytes:
    with Image.open(BytesIO(payload)) as source:
        image = source.convert("RGB")
    image.thumbnail((1280, 1280))
    if enhanced:
        image = ImageOps.autocontrast(image, cutoff=1)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


async def review_template_rasters(source: Path, gateway, directory: Path) -> dict:
    """Persist validated VL regions before the background-only PPTX is compiled."""
    raw = template_reference(source)
    model = inspect_template_backgrounds(raw)
    record = {
        "status": "completed",
        "source_sha256": digest(Path(source).read_bytes()),
        "model": gateway.settings.model_id,
        "assets": [],
        "regions": {},
    }
    with ZipFile(BytesIO(raw)) as package:
        names = set(package.namelist())
        assets = {
            row["asset"]
            for rows in model["parts"].values()
            for row in rows
            if row["action"] == "keep"
            and row["role"] == "background"
            and row["asset"].lower().endswith((".png", ".jpg", ".jpeg"))
            and row["box"][2] * row["box"][3] > 0.6
        }
        assets.update(_background_fill_assets(package))
        for asset in sorted(assets & names):
            payload = package.read(asset)
            if not smooth_light_canvas(payload):
                continue
            regions = []
            for enhanced in (False, True):
                view = _png_view(payload, enhanced)
                key = hashlib.sha256(
                    json.dumps(
                        {
                            "asset_sha256": digest(payload),
                            "view_sha256": digest(view),
                            "enhanced": enhanced,
                            "model": gateway.settings.model_id,
                            "endpoint": gateway.settings.base_url,
                            "prompt": PROMPT,
                            "system_prompt_sha256": digest(
                                (ROOT / "prompts/background_raster.md").read_bytes()
                            ),
                            "schema": RasterRegions.model_json_schema(),
                        },
                        sort_keys=True,
                    ).encode()
                ).hexdigest()
                target = Path(gateway.settings.data_dir) / "raster-review-cache" / f"{key}.json"
                try:
                    answer = RasterRegions.model_validate_json(target.read_text())
                except (OSError, ValueError):
                    answer = RasterRegions.model_validate(
                        await gateway.json_request(
                            "background_raster",
                            {"task": PROMPT, "view": "enhanced" if enhanced else "original"},
                            timeout=180,
                            schema=RasterRegions.model_json_schema(),
                            images=[view],
                        )
                    )
                    atomic_json(target, answer.model_dump())
                regions.extend(region.model_dump() for region in answer.regions)
            if not regions:
                regions = large_white_content_surface(payload)
            record["assets"].append({"asset": asset, "regions": len(regions)})
            if regions:
                record["regions"][asset] = regions
    atomic_json(Path(directory) / "raster-review.json", record)
    return record
