"""Local cache for previously verified font downloads."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from studio._vendor.font_extraction.pyapi.pipeline.reference_fonts import SFNT_FORMATS

ORIGINS = {"google-fonts", "fontsource", "microsoft-aptos"}
MAX_FONT_BYTES = 8 * 1024 * 1024


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as file:
        temporary = Path(file.name)
        try:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class FontDownloadCache:
    def __init__(self, directory: Path):
        self.directory = directory

    def _paths(self, family: str, weight: int, style: str) -> tuple[Path, Path]:
        key = hashlib.sha256(f"{family.casefold()}|{weight}|{style}".encode()).hexdigest()
        return self.directory / f"{key}.json", self.directory / f"{key}.font"

    def get(self, family: str, weight: int, style: str) -> dict | None:
        metadata_path, font_path = self._paths(family, weight, style)
        try:
            metadata = json.loads(metadata_path.read_text())
            size = font_path.stat().st_size
            if (
                metadata.get("family") != family
                or metadata.get("weight") != weight
                or metadata.get("style") != style
                or metadata.get("source") not in ORIGINS
                or size <= 0
                or size > MAX_FONT_BYTES
            ):
                return None
            data = font_path.read_bytes()
            if data[:4] not in SFNT_FORMATS:
                return None
            if hashlib.sha256(data).hexdigest() != metadata.get("sha256"):
                return None
            return {
                "family": family,
                "resolvedFamily": metadata.get("resolvedFamily") or family,
                "weight": weight,
                "style": style,
                "source": metadata["source"],
                "data": data,
            }
        except (OSError, ValueError, TypeError):
            return None

    def put(self, item: dict) -> None:
        family, weight, style = item["family"], item["weight"], item["style"]
        data = item["data"]
        if item["source"] not in ORIGINS or not 0 < len(data) <= MAX_FONT_BYTES:
            return
        metadata_path, font_path = self._paths(family, weight, style)
        metadata = {
            "schemaVersion": 1,
            "family": family,
            "resolvedFamily": item.get("resolvedFamily") or family,
            "weight": weight,
            "style": style,
            "source": item["source"],
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        _atomic_write(font_path, data)
        _atomic_write(metadata_path, json.dumps(metadata, sort_keys=True).encode())
