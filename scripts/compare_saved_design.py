"""Recompose an immutable reviewed package without analysis or inference calls."""

import argparse
import hashlib
import json
from pathlib import Path
import time

from studio.models import PreparedPackage, Plans, SlideScene
from studio.generation_composition import compose_generation
from studio.audit import audit_scenes
from studio.export_audit import audit_export
from studio.render import render_variant
from studio.design_balance import design_cost, composition_family, rhythm_cost
from studio.scene_regions import unused_body_regions


def metrics(scenes, package):
    body = [
        e
        for s in scenes
        for e in s.elements
        if e.kind == "text" and e.role == "body" and e.source_ids
    ]
    return {
        "body_font_mean": round(sum(e.size for e in body) / max(1, len(body)), 2),
        "body_font_min": min((e.size for e in body), default=0),
        "unused_regions": sum(unused_body_regions(s, package) for s in scenes),
        "design_cost": round(sum(design_cost(s, package.template) for s in scenes), 2),
        "rhythm_cost": rhythm_cost([composition_family(s, package.template) for s in scenes]),
        "chart_area": [
            round(e.box.w * e.box.h, 1) for s in scenes for e in s.elements if e.kind == "chart"
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package_dir", type=Path)
    parser.add_argument("baseline_dir", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    raw = (args.package_dir / "package.json").read_bytes()
    package = PreparedPackage.model_validate_json(raw)
    plans = Plans.model_validate_json((args.baseline_dir / "plans.json").read_bytes())
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    composed = compose_generation(plans, package, args.output)
    report = {
        "diversity": composed.diversity.model_dump(),
        "package_sha256": hashlib.sha256(raw).hexdigest(),
        "model_calls": 0,
        "composition_seconds": round(time.monotonic() - started, 3),
        "variants": [],
    }
    for variant in composed.plans.variants:
        scenes = composed.decks[variant.key]
        before = [
            SlideScene.model_validate(s)
            for s in json.loads((args.baseline_dir / variant.key / "slides.json").read_text())
        ]
        findings = audit_scenes(scenes, package)
        rendering = render_variant(
            scenes, package.template, args.package_dir / "input.pptx", args.output / variant.key
        )
        exported = audit_export(args.output / variant.key / "deck.pptx", variant, package)
        # The comparison may change geometry, never the prepared evidence or order.
        assert [s.source_ids for s in before] == [s.source_ids for s in scenes]
        assert [s.title for s in before] == [s.title for s in scenes]
        row = {
            "variant": variant.key,
            "before": metrics(before, package),
            "after": metrics(scenes, package),
            "findings": [f.model_dump() for f in findings],
            "export_findings": [
                f.model_dump() if hasattr(f, "model_dump") else f for f in exported
            ],
            "rendering": rendering,
        }
        report["variants"].append(row)
        (args.output / "comparison.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        print(
            json.dumps(
                {k: v for k, v in row.items() if k in ("variant", "before", "after")},
                ensure_ascii=False,
            ),
            flush=True,
        )
    report["total_seconds"] = round(time.monotonic() - started, 3)
    report["source_unchanged"] = raw == (args.package_dir / "package.json").read_bytes()
    (args.output / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    assert report["source_unchanged"]
    assert not [
        f
        for r in report["variants"]
        for f in r["findings"] + r["export_findings"]
        if f.get("severity") == "error"
    ], "See comparison.json"


if __name__ == "__main__":
    main()
