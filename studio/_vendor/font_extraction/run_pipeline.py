"""Extract a font model and usable font files from one PPTX/POTX."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from bootstrap import add_agentico_source

add_agentico_source()

from runtime import font_service  # noqa: E402 - bootstrap must set the app path first


async def run(source: Path, output: Path) -> dict:
    async with font_service() as service:
        model = await service.extract_file(source, output)
    return {
        "presentation": model.presentation,
        "modelPath": str(output / "font-model.json"),
        "slides": len(model.slides),
        "resolvedVariants": len(model.fontAssets),
        "unresolvedVariants": len(model.unresolved),
        "complete": not model.unresolved,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="PPTX/POTX file")
    parser.add_argument("--output", type=Path, required=True, help="Output directory")
    arguments = parser.parse_args()
    try:
        result = asyncio.run(run(arguments.input, arguments.output))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["complete"] else 3
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
