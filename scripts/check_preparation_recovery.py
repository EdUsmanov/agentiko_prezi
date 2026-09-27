"""Opt-in live preparation replay; never changes the original saved job."""

import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import shutil
import tempfile
from studio.config import Settings, ROOT
from studio.models import PreparedPackage, Fact
from studio.pipeline import prepare, load_package
from studio.store import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("package_id")
    args = parser.parse_args()
    live = Settings.from_env()
    source = Store(live.data_dir).directory(args.package_id)
    original = PreparedPackage.model_validate_json((source / "package.json").read_text())
    content = original.content.model_copy(deep=True)
    # Replay pre-induction blocks when available, not already-filtered body facts.
    blocks = original.analysis.get("document_structure", {}).get("source_blocks")
    if blocks:
        content.facts = [Fact.model_validate(block) for block in blocks]
        content.headings = [h for h in content.headings if not h.get("id")]
        content.directives = []
    root = Path(tempfile.mkdtemp(prefix="preparation-recovery-", dir=ROOT / "test-results"))
    settings = replace(live, data_dir=root / "data")

    class ProgressStore(Store):
        def update(self, jid, state=None, **fields):
            super().update(jid, state, **fields)
            if fields.get("phase"):
                print(fields["phase"], flush=True)

    store = ProgressStore(settings.data_dir)
    job = store.create("preparation", {"template_name": original.template.name})
    folder = store.directory(job["id"])
    shutil.copyfile(source / "input.pptx", folder / "input.pptx")
    print("RESULT_ROOT=" + str(root), flush=True)
    prepare(
        store,
        job["id"],
        "",
        original.constraints.audience,
        original.constraints.instructions,
        original.constraints.slides,
        settings,
        content_model=content,
        base_constraints=original.constraints,
    )
    result = store.get(job["id"])
    analysis = result.get("analysis", {})
    summary = {
        "state": result["state"],
        "error": result.get("error"),
        "job_id": job["id"],
        "template_status": analysis.get("template_semantics", {}).get("status"),
        "document_status": analysis.get("document_structure", {}).get("status"),
        "planning_status": analysis.get("planning_status"),
        "warnings": result.get("warnings", []),
        "model_calls": analysis.get("model_calls", []),
        "seconds": result.get("analysis_seconds"),
    }
    if result["state"] == "ready":
        from studio.document import structure_document

        class CacheProbe:
            def __init__(self):
                self.settings = settings
                self.calls = []
                self.requests = 0

            async def json_request(self, *args, **kwargs):
                self.requests += 1
                raise AssertionError("Cache probe must not call the network")

        probe = CacheProbe()
        replay = load_package(store, job["id"])
        replay.content = content.model_copy(deep=True)
        asyncio.run(structure_document(replay, probe))
        summary["document_cache_probe"] = {"requests": probe.requests, "hits": len(probe.calls)}
        assert probe.requests == 0, summary["document_cache_probe"]
    (root / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if result["state"] != "ready":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
