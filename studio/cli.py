import argparse
from pathlib import Path
import json
import os
from .config import Settings
from .examples import index_examples


def main():
    parser = argparse.ArgumentParser(description="Template-aware presentation service")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=int(os.getenv("STUDIO_PORT", "8765")))
    index = sub.add_parser("index-examples")
    index.add_argument("paths", nargs="+", type=Path)
    index.add_argument("--single-variant", type=Path, action="append", default=[])
    preanalysis = sub.add_parser("preanalyze-examples")
    preanalysis.add_argument("ids", nargs="*")
    args = parser.parse_args()
    if args.command == "index-examples":
        print(
            json.dumps(
                index_examples(
                    args.paths, Settings.from_env(), single_variant_paths=args.single_variant
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "preanalyze-examples":
        import asyncio
        from .examples import sources
        from .reference_analysis import preanalyze_reference
        from .store import Store

        settings = Settings.from_env()
        from .diagnostics import configure

        configure(settings.api_key)
        store = Store(settings.data_dir)
        with store.connect() as connection:
            active = connection.execute(
                "SELECT 1 FROM jobs WHERE state IN ('accepted','running')"
            ).fetchone()
        if active or store.scheduled():
            parser.error("Дождитесь завершения активных и запланированных заданий")

        async def run():
            for rid in args.ids or [r["id"] for r in sources(settings)]:
                result = await preanalyze_reference(
                    settings,
                    rid,
                    lambda message, percent: print(
                        json.dumps(
                            {"id": rid, "phase": message, "progress": percent}, ensure_ascii=False
                        ),
                        flush=True,
                    ),
                )
                print(json.dumps(result, ensure_ascii=False), flush=True)

        asyncio.run(run())
    else:
        import uvicorn
        from .app import create_app

        uvicorn.run(create_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
