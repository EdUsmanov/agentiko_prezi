"""Extract a background-only, editable PPTX from a PPTX/POTX template."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from bgextract import extract_background_pptx, inspect_template_backgrounds
from bgextract.vl_regions import review_raster_backgrounds


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Source .pptx or .potx")
    parser.add_argument("output", type=Path, help="Destination background-only .pptx")
    parser.add_argument(
        "--report", type=Path, help="Optional JSON with object decisions and reasons"
    )
    parser.add_argument(
        "--vl", action="store_true", help="Review content baked into smooth raster backgrounds"
    )
    parser.add_argument("--vl-model", default="qwen3.8-27b-noreason")
    parser.add_argument("--vl-cache", type=Path, help="Reuse VL decisions across retries")
    parser.add_argument(
        "--vl-url",
        default=os.getenv("VL_CHAT_COMPLETIONS_URL") or os.getenv("LLM_CHAT_COMPLETIONS_URL"),
    )
    args = parser.parse_args()
    if args.input.suffix.lower() not in {".pptx", ".potx"}:
        parser.error("input must be a .pptx or .potx file")
    if args.output.suffix.lower() != ".pptx":
        parser.error("output must be a .pptx file")
    if args.input.resolve() == args.output.resolve():
        parser.error("input and output must be different files")
    if args.report and args.report.resolve() in {args.input.resolve(), args.output.resolve()}:
        parser.error("report must be different from input and output")

    reference = args.input.read_bytes()
    model = inspect_template_backgrounds(reference)
    if args.vl:
        vl_api_key = os.getenv("VL_API_KEY") or os.getenv("LLM_API_KEY")
        if not args.vl_url or not vl_api_key:
            parser.error("--vl requires VL_CHAT_COMPLETIONS_URL and VL_API_KEY")
        review_raster_backgrounds(
            reference, model, args.vl_url, vl_api_key, args.vl_model, args.vl_cache
        )
    result = extract_background_pptx(reference, model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(result)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(model['slides'])} slides: {args.output}")


if __name__ == "__main__":
    main()

