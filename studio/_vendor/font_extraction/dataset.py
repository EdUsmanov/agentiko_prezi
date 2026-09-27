"""Deterministic OOXML-only slide/font dataset; no network or font binaries."""

import csv
import json
from pathlib import Path

from pyapi.pipeline.reference_font_usage import extract_reference_font_usage

FIELDS = (
    "presentation",
    "slide",
    "kind",
    "family",
    "script",
    "role",
    "source",
    "weight",
    "italic",
    "sizesPt",
    "characters",
    "runCount",
    "shapeIds",
)


def presentation_files(path: Path) -> list[Path]:
    """List a single PPTX/POTX or all direct child presentations in a directory."""
    if path.is_file():
        if path.suffix.lower() not in {".pptx", ".potx"}:
            raise ValueError("Input must be a PPTX or POTX file")
        return [path]
    if not path.is_dir():
        raise ValueError(f"Input does not exist or is not a directory: {path}")
    files = sorted(
        item
        for item in path.iterdir()
        if item.is_file() and item.suffix.lower() in {".pptx", ".potx"}
    )
    if not files:
        raise ValueError(f"No PPTX/POTX files found in {path}")
    return files


def build_font_dataset(source: Path, output: Path) -> tuple[int, int, int]:
    """Write presentations.jsonl and slide-fonts.csv; return file, slide, row counts."""
    results = [
        extract_reference_font_usage(path.read_bytes(), path.name)
        for path in presentation_files(source)
    ]
    output.mkdir(parents=True, exist_ok=True)
    with (output / "presentations.jsonl").open("w", encoding="utf-8") as stream:
        for result in results:
            stream.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    row_count = 0
    with (output / "slide-fonts.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for result in results:
            for slide in result["slides"]:
                for kind, field in (("used", "fontUses"), ("hint", "fontHints")):
                    for item in slide[field]:
                        writer.writerow(
                            {
                                "presentation": result["presentation"],
                                "slide": slide["number"],
                                "kind": kind,
                                **{
                                    key: json.dumps(item[key], ensure_ascii=False)
                                    if key in {"sizesPt", "shapeIds"}
                                    else item[key]
                                    for key in FIELDS[3:]
                                },
                            }
                        )
                        row_count += 1
    return len(results), sum(item["slideCount"] for item in results), row_count
