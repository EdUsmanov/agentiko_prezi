import argparse
from pathlib import Path
import json
import os
from .config import Settings
from studio.templates.examples import index_examples


def main():
    parser = argparse.ArgumentParser(description="Template-aware presentation service")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=int(os.getenv("STUDIO_PORT", "8765")))
    index = sub.add_parser("index-examples")
    index.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    if args.command == "index-examples":
        print(
            json.dumps(
                index_examples(args.paths, Settings.from_env()), ensure_ascii=False, indent=2
            )
        )
    else:
        import uvicorn
        from .app import create_app

        uvicorn.run(create_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
