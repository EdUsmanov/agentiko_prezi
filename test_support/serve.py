"""Launch a real app with explicit isolated settings, never .env or working data."""

import argparse
import json
import os
from pathlib import Path
import uvicorn
from studio.app import create_app
from studio.config import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    values = json.loads(args.settings.read_text())
    values["data_dir"] = Path(values["data_dir"])
    if values.get("api_key") and not (
        values.get("mode") == "api"
        and values.get("execution_kind") == "live"
        and os.environ.get("STUDIO_TEST_LIVE_PROXY_URL")
    ):
        raise ValueError("Browser tests must not use a provider credential")
    uvicorn.run(
        create_app(Settings(**values)), host="127.0.0.1", port=args.port, log_level="warning"
    )


if __name__ == "__main__":
    main()
