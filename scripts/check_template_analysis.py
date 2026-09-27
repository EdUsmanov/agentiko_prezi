"""Explicit live regression of the real user-template analysis stage."""

import argparse
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument(
        "--layouts-only", action="store_true", help="Diagnose only image-free master layouts"
    )
    args = parser.parse_args()
    if (ROOT / "scripts/run_experiment.py").exists():
        from scripts.run_experiment import experiment_environment

        os.environ.update(experiment_environment(ROOT.parent / "presentation-studio/.env"))
    from studio.config import Settings
    from studio.template import analyze_template
    from studio.analysis import template_inventory, reference_images, analyze_meaning
    from studio.gateway import ModelGateway
    from studio.store import Store
    from studio.diagnostics import configure, scope
    from studio.cache_version import atomic_json

    output = Path(tempfile.mkdtemp(prefix="template-analysis-smoke-", dir=ROOT / "test-results"))
    store = Store(output / "data")
    settings = replace(Settings.from_env(), data_dir=store.root)
    configure(settings.api_key)
    job = store.create("preparation", {"template_name": args.template.name})
    folder = store.directory(job["id"])
    print(json.dumps({"output": str(output), "job_id": job["id"]}), flush=True)

    def progress(phase):
        store.update(job["id"], "running", phase=phase)
        print(phase, flush=True)

    started = time.monotonic()
    with scope(store, job["id"]):
        profile = analyze_template(
            args.template,
            folder,
            allow_download=settings.download_fonts,
            font_progress=progress,
        )
        missing = [f for f in profile.missing_fonts if f.get("required_for_generation", True)]
        if missing:
            store.update(job["id"], "waiting_fonts", missing_fonts=missing)
            raise SystemExit(2)
        images = reference_images(args.template, profile, folder)
        gateway = ModelGateway(settings)
        inventory = template_inventory(args.template, profile)
        if args.layouts_only:
            inventory["patterns"] = [p for p in inventory["patterns"] if not p["source_slide"]]
        report = asyncio.run(analyze_meaning(inventory, gateway, images, progress))
        report.update(
            seconds=round(time.monotonic() - started, 3),
            model_calls=gateway.calls,
            patterns_total=len(inventory["patterns"]),
            template_name=args.template.name,
            scope="layouts_only" if args.layouts_only else "all_patterns",
        )
        atomic_json(output / "result.json", report)
        store.update(
            job["id"],
            "ready" if report["status"] == "completed" else "failed",
            phase="Проверка анализа завершена",
            result=str(output / "result.json"),
            analysis_status=report["status"],
        )
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "seconds": report["seconds"],
                    "verified": len(report["patterns"]),
                    "total": len(inventory["patterns"]),
                    "report": str(output / "result.json"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if report["status"] != "completed":
            raise SystemExit(2)


if __name__ == "__main__":
    main()
