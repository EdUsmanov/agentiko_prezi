"""Offline native-backend regression on the synthetic story and a real template.

This checks composition/export, not the external DeepPresenter model.
Accepts the before-runtime.json checkpoint produced by the experiment harness.
"""

import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.models import PreparedPackage
from studio.composer import compose_variant
from studio.audit import audit_scenes, repair_scenes
from studio.render import render_variant
from studio.powerpoint import open_presentation
from studio.export_audit import evidence, geometry


def check(fixture):
    package = PreparedPackage.model_validate_json((fixture / "before-runtime.json").read_text())
    if package.content.title != "Единый сервис заявок":
        raise ValueError("Expected the synthetic regression, not arbitrary user facts")
    directory = Path(tempfile.mkdtemp(prefix="native-fixture-", dir=ROOT / "test-results"))
    print("Report directory:", directory, flush=True)
    findings = []
    for variant in package.prepared_plans.variants:
        scenes = compose_variant(variant, package)
        repair_scenes(scenes, package)
        target = directory / variant.key
        rendering = render_variant(scenes, package.template, fixture / "input.pptx", target)
        if not rendering["native_render"]:
            raise ValueError("A real PPTX render is required for this check")
        prs = open_presentation(target / "deck.pptx")
        actual, _ = geometry(prs, package.template)
        rows = (
            actual
            + evidence(prs, variant, package)
            + [f.model_dump() for f in audit_scenes(scenes, package)]
        )
        findings.extend({"variant": variant.key, **f} for f in rows)
        print(
            variant.key,
            "slides:",
            len(prs.slides),
            "errors:",
            sum(f["severity"] == "error" for f in rows),
            flush=True,
        )
    report = {
        "scope": "native_composition_and_export; no_model_calls",
        "findings": findings,
        "errors": sum(f["severity"] == "error" for f in findings),
    }
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    print(json.dumps(check(Path(sys.argv[1])), ensure_ascii=False, indent=2))
