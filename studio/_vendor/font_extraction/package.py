"""Build a reproducible standalone ZIP with the extractor and sample dataset."""

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

HERE = Path(__file__).resolve().parent
FILES = (
    "README.md",
    "CONTRACTS.md",
    "bootstrap.py",
    "contracts.py",
    "dataset.py",
    "delivery.py",
    "runtime.py",
    "decoder.py",
    "decoder.mjs",
    "requirements.txt",
    "vendor/mtx-decompressor/index.mjs",
    "vendor/mtx-decompressor/LICENSE",
    "run_pipeline.py",
    "run_corpus.py",
    "extract.py",
    "package.py",
    "schemas/font-model.schema.json",
    "schemas/batch-report.schema.json",
    "dataset/presentations.jsonl",
    "dataset/slide-fonts.csv",
)
MODULES = (
    "pyapi/__init__.py",
    "pyapi/application/__init__.py",
    "pyapi/application/ports.py",
    "pyapi/application/reference_font_pipeline.py",
    "pyapi/domain/__init__.py",
    "pyapi/domain/archive_safety.py",
    "pyapi/domain/font_model.py",
    "pyapi/domain/font_names.py",
    "pyapi/infrastructure/__init__.py",
    "pyapi/infrastructure/font_download_cache.py",
    "pyapi/infrastructure/font_downloads.py",
    "pyapi/infrastructure/open_fonts.py",
    "pyapi/pipeline/__init__.py",
    "pyapi/pipeline/ooxml_resolution.py",
    "pyapi/pipeline/reference_font_usage.py",
    "pyapi/pipeline/reference_font_usage_styles.py",
    "pyapi/pipeline/reference_fonts.py",
    "pyapi/pipeline/reference_profile.py",
)


def _module_data(relative: str) -> bytes:
    bundled = HERE / relative
    if bundled.is_file():
        return bundled.read_bytes()
    if relative == "pyapi/pipeline/reference_profile.py":
        return (HERE / "standalone_reference_profile.py").read_bytes()
    source = HERE.parents[1] / "app/apps/pyapi" / relative
    if source.is_file():
        return source.read_bytes()
    raise FileNotFoundError(f"Extractor source module is missing: {relative}")


def _add(archive: ZipFile, name: str, data: bytes) -> None:
    entry = ZipInfo(f"font-extraction/{name}", date_time=(2026, 1, 1, 0, 0, 0))
    entry.compress_type = ZIP_DEFLATED
    entry.external_attr = 0o644 << 16
    archive.writestr(entry, data)


def package(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"formatVersion": 2, "selfContainedSources": True, "files": {}}
    with ZipFile(output, "w") as archive:
        for relative in (*FILES, *MODULES):
            data = _module_data(relative) if relative in MODULES else (HERE / relative).read_bytes()
            _add(archive, relative, data)
            manifest["files"][relative] = hashlib.sha256(data).hexdigest()
        _add(
            archive,
            "MANIFEST.json",
            (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="ZIP file path")
    arguments = parser.parse_args()
    print(package(arguments.output))


if __name__ == "__main__":
    main()
