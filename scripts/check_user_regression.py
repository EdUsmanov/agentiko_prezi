"""Recheck an explicitly authorized saved case in an isolated test store.

No production job, source upload or library snapshot is overwritten.
--live sends this case to the configured provider; explicit approval is required.
"""

import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def run(args):
    if (ROOT / "scripts/run_experiment.py").exists():
        import os
        from scripts.run_experiment import experiment_environment

        os.environ.update(experiment_environment(ROOT.parent / "presentation-studio/.env"))
    from studio.config import Settings
    from studio.models import PreparedPackage, Fact
    from studio.jobs.store import Store
    from studio.providers.gateway import ModelGateway
    from studio.security import digest
    from studio.diagnostics import configure, scope

    fixture = args.fixture.resolve()
    output = Path(tempfile.mkdtemp(prefix="user-regression-", dir=ROOT / "test-results"))
    print("OUTPUT", output, flush=True)
    store = Store(output / "data")
    settings = replace(
        Settings.from_env(),
        data_dir=store.root,
        mode="api" if args.live else "extractive",
        engine=(
            "pptagent_v02" if (ROOT / "studio/pptagent_engine.py").exists() else "deeppresenter"
        )
        if args.live
        else "native",
        visual_review=args.live,
    )
    configure(settings.api_key)
    job = store.create("preparation", {"template_name": "Authorized regression"})
    target = store.directory(job["id"])
    shutil.copytree(fixture, target, dirs_exist_ok=True)
    raw = json.loads((target / "package.json").read_text())

    def relocate(value):
        if isinstance(value, dict):
            return {k: relocate(v) for k, v in value.items()}
        if isinstance(value, list):
            return [relocate(v) for v in value]
        if isinstance(value, str) and value.startswith(str(fixture) + "/"):
            return str(target) + value[len(str(fixture)) :]
        return value

    package = PreparedPackage.model_validate(relocate(raw))
    package.id = job["id"]
    from studio.contents.parsing import plain_inline

    for table in package.content.tables:
        table.headers = [plain_inline(c) for c in table.headers]
        table.rows = [[plain_inline(c) for c in row] for row in table.rows]
        table.visualization = "auto"
    original = package.analysis.get("document_structure", {}).get("source_blocks")
    if original:
        package.content.facts = [Fact.model_validate(f) for f in original]
        package.content.headings = []
        package.content.directives = []
    from studio.composition.powerpoint import open_presentation
    from studio.templates.native_template import native_patterns, compile_backgrounds
    from studio.templates.native_style import native_styles
    from studio.composition.contracts import apply_meanings

    source = target / "input.pptx"
    previous = {p.id: p for p in package.template.patterns}
    patterns = native_patterns(open_presentation(source), native_styles(source))
    # Retain verified source meaning, refresh physical bounds with current code.
    for pattern in patterns:
        old = previous.get(pattern.id)
        if old:
            for key in (
                "purpose",
                "reusable",
                "role",
                "table_style",
                "background_image",
                "reference_image",
                "background",
                "foreground",
                "title_foreground",
                "title_background",
                "zone_backgrounds",
                "zone_foregrounds",
            ):
                setattr(pattern, key, getattr(old, key))
    package.template.patterns = patterns
    print("Refreshing template geometry", flush=True)
    if not args.reuse_backgrounds:
        compile_backgrounds(package.template, source, target)
    semantics = package.analysis.get("template_semantics", {})
    apply_meanings(package.template, semantics)
    package.analysis = {
        "template_semantics": semantics,
        "warnings": [],
        "model_mode": settings.mode,
        "test_scope": "saved_authorized_case; refreshed geometry and content analysis",
    }
    gateway = ModelGateway(settings)
    from studio.contents.document import structure_document
    from studio.contents.sections import prepare_sections
    from studio.contents.archetypes import analyze_content_archetypes
    from studio.contents.storyboard import prepare_storyboard
    from studio.contents.planner import plan
    from studio.contents.semantic_bindings import canonicalize_storyboard

    started = time.monotonic()
    with scope(store, job["id"]):
        await structure_document(package, gateway, lambda s: print(s, flush=True))
        await prepare_sections(package, gateway)
        await analyze_content_archetypes(package, gateway, lambda s: print(s, flush=True))
        prepare_storyboard(package)
        package.prepared_plans, warning = await plan(package, gateway, 600)
        canonicalize_storyboard(package.prepared_plans, package)
        package.analysis.update(
            planning_source=package.analysis.get("planning_method")
            or ("model" if args.live and not warning else "extractive"),
            planning_status="degraded" if warning else "completed",
            model_calls=gateway.calls,
        )
        if warning:
            package.analysis["warnings"].append(warning)
        (target / "analysis-checkpoint.json").write_text(package.model_dump_json(indent=2))
        if settings.engine == "pptagent_v02":
            from studio.pptagent_engine import prepare

            await prepare(package, source, gateway, lambda s: print(s, flush=True))
    package.manifest["analysis_seconds"] = round(time.monotonic() - started, 3)
    package.manifest["template_layers"] = {
        str(p.relative_to(target)): digest(p.read_bytes())
        for folder in ("template-layers", "source-review")
        for p in (target / folder).rglob("*")
        if p.is_file()
    }
    encoded = package.model_dump_json(indent=2)
    (target / "package.json").write_text(encoded)
    store.update(job["id"], "ready", package_hash=digest(encoded.encode()))
    print(
        "PREPARATION",
        job["id"],
        "SLIDES",
        len(package.prepared_plans.variants[0].slides),
        flush=True,
    )
    generation = store.create("generation", {"package_id": package.id, "deadline_at": None})
    print("GENERATION", generation["id"], flush=True)
    from studio.pipeline import generate

    with scope(store, generation["id"]):
        try:
            await generate(store, generation["id"], settings)
        except Exception as error:
            from studio.diagnostics import exception

            exception("regression.failed", error)
            store.update(generation["id"], "failed", error=str(error))
            raise
    result = store.get(generation["id"])
    print(
        json.dumps(
            {k: result.get(k) for k in ("id", "state", "errors", "elapsed_seconds", "warnings")},
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--reuse-backgrounds",
        action="store_true",
        help="Reuse byte-identical rendered source artwork; geometry is always refreshed",
    )
    asyncio.run(run(parser.parse_args()))
