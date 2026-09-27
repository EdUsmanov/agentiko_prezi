"""Extract an OOXML-only presentation/slide/font dataset without downloads."""

import argparse
import json
import sys
from pathlib import Path

from bootstrap import add_agentico_source

add_agentico_source()

from dataset import build_font_dataset  # noqa: E402 - bootstrap must set the app path first


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="PPTX/POTX file or directory")
    parser.add_argument("--output", type=Path, required=True, help="Output directory")
    arguments = parser.parse_args()
    try:
        presentations, slides, rows = build_font_dataset(arguments.input, arguments.output)
        print(
            json.dumps(
                {"presentations": presentations, "slides": slides, "fontRows": rows},
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
