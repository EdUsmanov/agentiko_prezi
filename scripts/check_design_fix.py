"""Local visual regression using an immutable input package; no inference calls."""

import argparse
import json
from studio.config import Settings, ROOT
from studio.store import Store
from studio.pipeline import load_package
from studio.template import analyze_template
from studio.native_template import compile_backgrounds
from studio.planner import extractive_plans, assign_compositions
from studio.composer import compose_variant
from studio.audit import audit_scenes, repair_scenes
from studio.render import render_variant


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("package")
    args = parser.parse_args()
    settings = Settings.from_env()
    store = Store(settings.data_dir)
    package = load_package(store, args.package)
    target = ROOT / "test-results/design-fix" / args.package
    target.mkdir(parents=True, exist_ok=True)
    source = store.directory(args.package) / "input.pptx"
    package.template = analyze_template(source, target)
    compile_backgrounds(package.template, source, target)
    plans = assign_compositions(extractive_plans(package), package)
    report = []
    for variant in plans.variants:
        scenes = compose_variant(variant, package)
        repair_scenes(scenes, package)
        findings = audit_scenes(scenes, package)
        rendering = render_variant(scenes, package.template, source, target / variant.key)
        report.append(
            {
                "variant": variant.key,
                "patterns": [s.pattern_id for s in scenes],
                "findings": [f.model_dump() for f in findings],
                "rendering": rendering,
            }
        )
    (target / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
