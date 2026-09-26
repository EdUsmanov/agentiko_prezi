"""CLI for conservative text-zone search on one image or a background corpus."""

from __future__ import annotations

import argparse
import json
from fnmatch import fnmatch
from hashlib import sha256
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from textzone import analyze_image
from textzone.vl import load_or_query_cells


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="PNG or directory of PNG backgrounds")
    parser.add_argument("output", type=Path, help="Directory for report and overlays")
    parser.add_argument("--model", type=Path, help="Explicit extractor model.json")
    parser.add_argument("--slide", type=int, help="Slide number for a single PNG")
    parser.add_argument(
        "--vl", action="store_true", help="Call the no-reason VL model on cache misses"
    )
    parser.add_argument(
        "--vl-cache", type=Path, help="Existing or destination VL response directory"
    )
    parser.add_argument("--no-overlays", action="store_true", help="Only write report.json")
    parser.add_argument(
        "--include",
        action="append",
        metavar="GLOB",
        help="Process matching paths only; repeat for multiple patterns",
    )
    return parser.parse_args()


def _images(source: Path, output: Path) -> list[Path]:
    if source.is_file():
        if source.suffix.lower() != ".png":
            raise ValueError("Input image must be a PNG")
        return [source]
    if not source.is_dir():
        raise FileNotFoundError(source)
    files = sorted(source.rglob("*.png"))
    # Allow output under input without accidentally auditing last run's overlays.
    files = [path for path in files if not path.is_relative_to(output)]
    if not files:
        raise ValueError("No PNG backgrounds found")
    return files


def _model_path(image: Path, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    candidates = [image.parent / "model.json", image.parent.parent / "model.json"]
    return next((path for path in candidates if path.exists()), None)


def _slide_model(
    image: Path,
    model_path: Path | None,
    explicit_number: int | None,
    model_cache: dict[Path, dict[int, dict[str, Any]]],
) -> dict[str, Any] | None:
    if model_path is None:
        return None
    if model_path not in model_cache:
        model = json.loads(model_path.read_text())
        model_cache[model_path] = {
            int(slide["sourceSlideNumber"]): slide for slide in model["slides"]
        }
    if explicit_number is not None:
        number = explicit_number
    elif image.stem.isdigit():
        number = int(image.stem)
    else:
        raise ValueError(f"Cannot match {image.name} to model; pass --slide")
    try:
        return model_cache[model_path][number]
    except KeyError as error:
        raise ValueError(f"Slide {number} missing from {model_path}") from error


def _font() -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", 20)
    except OSError:
        return ImageFont.load_default()


def _overlay(source: Image.Image, label: str, result: dict[str, Any], destination: Path) -> None:
    image = source.convert("RGB")
    canvas = Image.new("RGB", (image.width, image.height + 48), "#101010")
    canvas.paste(image, (0, 48))
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 9), label, font=_font(), fill="white")
    if result["box"] is None:
        draw.text((30, 100), "NO SAFE RECTANGLE", font=_font(), fill="#ff6666")
    else:
        left, top, right, bottom = result["box"]
        draw.rectangle((left, top + 48, right, bottom + 48), outline="#52ff72", width=5)
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination)


def main() -> None:
    args = _args()
    source, output = args.input.resolve(), args.output.resolve()
    images = _images(source, output)
    if args.include:
        images = [
            path
            for path in images
            if any(
                fnmatch(path.name if source.is_file() else str(path.relative_to(source)), pattern)
                for pattern in args.include
            )
        ]
        if not images:
            raise ValueError("No PNG backgrounds matched --include")
    output.mkdir(parents=True, exist_ok=True)
    cache_dir = args.vl_cache or output / "responses"
    model_cache: dict[Path, dict[int, dict[str, Any]]] = {}
    vl_cache: dict[bytes, set[str] | None] = {}
    report: dict[str, dict[str, Any]] = {}
    for index, path in enumerate(images, 1):
        key = path.name if source.is_file() else str(path.relative_to(source))
        model = _slide_model(path, _model_path(path, args.model), args.slide, model_cache)
        contents = path.read_bytes()
        with Image.open(path) as opened:
            image = opened.copy()
        preliminary = analyze_image(image, model)
        if preliminary["recognition_mode"] == "flat_pixel_mask":
            # Exact pixels and OOXML settle flat slides. VL cannot change
            # this branch, so a request would only spend time and credits.
            result = preliminary
        else:
            # Many template slides reuse identical backgrounds. Ask VL once.
            fingerprint = sha256(contents).digest()
            if fingerprint not in vl_cache:
                vl_cache[fingerprint] = load_or_query_cells(contents, image, cache_dir, args.vl)
            result = analyze_image(image, model, vl_cache[fingerprint])
        report[key] = result
        if not args.no_overlays:
            _overlay(image, key, result, output / "overlays" / key)
        if index % 20 == 0:
            print(f"Processed {index}/{len(images)} slides", flush=True)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "slides": len(report),
                "rectangles": sum(v["box"] is not None for v in report.values()),
                "abstentions": [key for key, value in report.items() if value["box"] is None],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

