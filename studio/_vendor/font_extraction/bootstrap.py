"""Load the bundled extractor modules, or their source checkout during development."""

import sys
from pathlib import Path


def add_agentico_source() -> Path:
    bundle = Path(__file__).resolve().parent
    if (bundle / "pyapi").is_dir():
        sys.path.insert(0, str(bundle))
        return bundle
    source = bundle.parents[1] / "app/apps/pyapi"
    if (source / "pyapi").is_dir():
        sys.path.insert(0, str(source))
        return source
    raise RuntimeError("Bundled extractor modules are missing; unpack the complete archive")
