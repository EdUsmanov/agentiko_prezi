"""Extract font models and files from every PPTX/POTX in a directory."""

import argparse
import asyncio
import sys
from pathlib import Path

from bootstrap import add_agentico_source

add_agentico_source()

from contracts import FontFileResult  # noqa: E402 - bootstrap must set the app path first
from runtime import font_service  # noqa: E402 - bootstrap must set the app path first


async def run(source: Path, output: Path) -> int:
    async with font_service() as service:

        def progress(number: int, total: int, item: FontFileResult) -> None:
            print(
                f"[{number}/{total}] {item.presentation}: {item.status}, "
                f"{item.slides} slides, {item.unresolvedVariants} unresolved",
                file=sys.stderr,
                flush=True,
            )

        report = await service.extract_directory(source, output, progress)
    print(report.model_dump_json(indent=2, exclude_none=True))
    if report.counts.failed:
        return 1
    return 3 if report.counts.unresolvedVariants else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Directory with PPTX/POTX files")
    parser.add_argument("--output", type=Path, required=True, help="Output directory")
    arguments = parser.parse_args()
    try:
        return asyncio.run(run(arguments.input, arguments.output))
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
