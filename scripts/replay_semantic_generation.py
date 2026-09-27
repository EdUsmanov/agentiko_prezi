"""Regression only: replay recorded semantic analysis, reparse source, run live generation.

Not a production-cache migration. The report explicitly labels replayed model analysis.
"""

import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import shutil
import tempfile
import time
from studio.config import ROOT, Settings
from studio.models import PreparedPackage
from studio.template import analyze_template
from studio.native_template import compile_backgrounds
from studio.analysis import reference_images
from studio.contracts import apply_meanings
from studio.storyboard import prepare_storyboard
from studio.planner import extractive_plans, assign_compositions, validate_plans
from studio.pipeline import generate, versions
from studio.store import Store
from studio.security import digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prepared_package")
    args = parser.parse_args()
    original = Path(args.prepared_package).resolve()
    p = PreparedPackage.model_validate_json(original.read_text())
    root = Path(tempfile.mkdtemp(prefix="semantic-replay-", dir=ROOT / "test-results"))
    settings = replace(Settings.from_env(), data_dir=root / "data")

    class ProgressStore(Store):
        def update(self, jid, state=None, **fields):
            super().update(jid, state, **fields)
            if fields.get("phase"):
                print(fields["phase"], flush=True)

    store = ProgressStore(settings.data_dir)
    job = store.create("preparation", {"template_name": p.template.name})
    folder = store.directory(job["id"])
    shutil.copyfile(original.parent / "input.pptx", folder / "input.pptx")
    print("RESULT_ROOT=" + str(root), flush=True)
    started = time.monotonic()
    profile = analyze_template(folder / "input.pptx", folder, allow_download=False)
    profile.name = p.template.name
    compile_backgrounds(profile, folder / "input.pptx", folder)
    reference_images(folder / "input.pptx", profile, folder)
    p.id = job["id"]
    p.template = profile
    for image in p.images:
        target = folder / "input-images" / Path(image.path).name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(image.path, target)
        image.path = str(target)
    apply_meanings(profile, p.analysis["template_semantics"])
    recorded_plans = p.prepared_plans
    p.analysis.pop("storyboard", None)
    prepare_storyboard(p)
    # Same facts and chapters, new deterministic structural contracts.
    plans = extractive_plans(p)
    model_plan_replayed = False
    if recorded_plans and all(
        [s.fact_ids for s in old.slides] == [s.fact_ids for s in new.slides]
        for old, new in zip(recorded_plans.variants, plans.variants)
    ):
        for old, new in zip(recorded_plans.variants, plans.variants):
            for previous, current in zip(old.slides, new.slides):
                if current.purpose == "content" and previous.title != p.content.title:
                    current.title = previous.title
                if current.layout != "divider":
                    current.layout = previous.layout
        model_plan_replayed = True
    p.prepared_plans = assign_compositions(validate_plans(plans, p), p)
    p.analysis.update(
        planning_source="model" if model_plan_replayed else "extractive",
        planning_status="completed",
        regression_analysis_replay=True,
    )
    p.manifest["versions"] = versions()
    p.manifest["analysis_seconds"] = round(time.monotonic() - started, 3)
    p.manifest["regression_analysis_replay"] = str(original)
    p.manifest["template_layers"] = {
        str(Path(f).relative_to(folder)): digest(Path(f).read_bytes())
        for pattern in profile.patterns
        for f in (pattern.background_image, pattern.reference_image)
        if f
    }
    raw = p.model_dump_json(indent=2)
    (folder / "package.json").write_text(raw)
    store.update(p.id, "ready", package_hash=digest(raw.encode()))
    generation = store.create("generation", {"package_id": p.id, "deadline_at": time.time() + 300})
    try:
        asyncio.run(generate(store, generation["id"], settings))
    except Exception as exc:
        store.update(generation["id"], "failed", error=type(exc).__name__ + ": " + str(exc))
        raise
    result = store.get(generation["id"])
    summary = {
        k: result.get(k)
        for k in (
            "state",
            "errors",
            "elapsed_seconds",
            "analysis_seconds",
            "warnings",
            "refinement",
        )
    }
    summary.update(
        preparation=p.id, generation=generation["id"], root=str(root), model_analysis_replayed=True
    )
    (root / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
