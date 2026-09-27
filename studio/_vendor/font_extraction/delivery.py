"""File and directory delivery for the reference-font application pipeline."""

from __future__ import annotations

import csv
import hashlib
import logging
import os
import tempfile
import zipfile
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree as ET

from contracts import BatchFontReport, FontFileResult
from dataset import presentation_files

from pyapi.application.ports import FontDecoder, FontVariantResolver
from pyapi.application.reference_font_pipeline import build_reference_font_data
from pyapi.domain.font_model import PresentationFontData
from pyapi.pipeline.reference_profile import _ordered_slides

logger = logging.getLogger(__name__)
CSV_FIELDS = (
    "presentation",
    "status",
    "slides",
    "resolvedVariants",
    "unresolvedVariants",
    "warningCount",
    "modelPath",
    "error",
)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as file:
        temporary = Path(file.name)
        try:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_model(source: Path, model: PresentationFontData, binaries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(source) as package:
        presentation = ET.fromstring(package.read("ppt/presentation.xml"))
        slide_count = len(_ordered_slides(package, set(package.namelist()), presentation))
    if [slide.number for slide in model.slides] != list(range(1, slide_count + 1)):
        raise ValueError("Font model does not contain every slide in presentation order")
    asset_by_id = {asset.id: asset for asset in model.fontAssets}
    if len(asset_by_id) != len(model.fontAssets):
        raise ValueError("Font asset IDs are not unique")
    for asset in model.fontAssets:
        relative = Path(asset.path)
        if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "fonts":
            raise ValueError(f"Invalid font asset path: {asset.path}")
        data = binaries.get(asset.path)
        if data is None or len(data) != asset.bytes:
            raise ValueError(f"Font asset is absent or truncated: {asset.path}")
        if hashlib.sha256(data).hexdigest() != asset.sha256:
            raise ValueError(f"Font asset checksum mismatch: {asset.path}")
    missing = set()
    for slide in model.slides:
        for uses in (slide.elements or {}).values():
            for use in uses:
                if use.status == "missing":
                    if use.assetId is not None:
                        raise ValueError("Missing font use has an asset ID")
                    missing.add((use.family, use.weight, use.style))
                elif use.assetId not in asset_by_id:
                    raise ValueError("Resolved font use has no asset")
                elif asset_by_id[use.assetId].source != use.status:
                    raise ValueError("Font use status does not match asset source")
    expected = {(item.family, item.weight, item.style) for item in model.unresolved}
    if missing != expected:
        raise ValueError("Unresolved variants do not match missing font uses")


def _write_report(report: BatchFontReport, output: Path) -> None:
    _atomic_write(
        output / "summary.json",
        report.model_dump_json(indent=2, exclude_none=True).encode("utf-8"),
    )
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for item in report.presentations:
            writer.writerow({key: getattr(item, key) for key in CSV_FIELDS})
        stream.seek(0)
        _atomic_write(output / "summary.csv", stream.read().encode("utf-8"))


class FontExtractionService:
    """Inject a decoder and variant resolver; no infrastructure is created here."""

    def __init__(self, decoder: FontDecoder, resolver: FontVariantResolver):
        self.decoder = decoder
        self.resolver = resolver

    async def extract_bytes(
        self, data: bytes, presentation_name: str
    ) -> tuple[PresentationFontData, dict[str, bytes]]:
        """Return the Pydantic model and font files keyed by safe relative paths."""
        return await build_reference_font_data(data, presentation_name, self.decoder, self.resolver)

    async def extract_file(self, source: Path, output: Path) -> PresentationFontData:
        """Write fonts first, then atomically publish font-model.json."""
        if not source.is_file() or source.suffix.lower() not in {".pptx", ".potx"}:
            raise ValueError("Input must be an existing PPTX or POTX file")
        model, binaries = await self.extract_bytes(source.read_bytes(), source.name)
        _validate_model(source, model, binaries)
        output.mkdir(parents=True, exist_ok=True)
        for asset in model.fontAssets:
            destination = output / asset.path
            _atomic_write(destination, binaries[asset.path])
            if hashlib.sha256(destination.read_bytes()).hexdigest() != asset.sha256:
                raise ValueError(f"Saved font asset checksum mismatch: {asset.path}")
        _atomic_write(
            output / "font-model.json",
            model.model_dump_json(indent=2, exclude_none=True).encode("utf-8"),
        )
        current_paths = {asset.path for asset in model.fontAssets}
        fonts_directory = output / "fonts"
        if fonts_directory.is_dir():
            for path in fonts_directory.iterdir():
                if (path.is_file() or path.is_symlink()) and str(path.relative_to(output)) not in current_paths:
                    path.unlink()
        return model

    async def extract_directory(
        self,
        source: Path,
        output: Path,
        on_result: Callable[[int, int, FontFileResult], None] | None = None,
    ) -> BatchFontReport:
        """Process all direct child PPTX/POTX files and continue after per-file errors."""
        if not source.is_dir():
            raise ValueError(f"Input must be a directory: {source}")
        files = presentation_files(source)
        stem_counts = Counter(path.stem for path in files)
        output.mkdir(parents=True, exist_ok=True)
        results: list[FontFileResult] = []
        for number, file in enumerate(files, start=1):
            destination = output / (file.stem if stem_counts[file.stem] == 1 else file.name)
            try:
                model = await self.extract_file(file, destination)
                result = FontFileResult(
                    presentation=file.name,
                    status="ok",
                    slides=len(model.slides),
                    resolvedVariants=len(model.fontAssets),
                    unresolvedVariants=len(model.unresolved),
                    warningCount=len(model.warnings),
                    modelPath=str((destination / "font-model.json").relative_to(output)),
                    unresolved=model.unresolved,
                )
            except Exception as error:
                logger.exception("Font extraction failed for %s", file.name)
                result = FontFileResult(
                    presentation=file.name,
                    status="error",
                    error=f"{type(error).__name__}: {error}",
                )
            results.append(result)
            _write_report(BatchFontReport.from_results(results), output)
            if on_result is not None:
                on_result(number, len(files), result)
        return BatchFontReport.from_results(results)
