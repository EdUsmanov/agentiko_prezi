import json
from types import SimpleNamespace

import pytest

from studio.checks.review_snapshot import (
    build_snapshot,
    load_snapshot,
    save_snapshot,
    snapshot_hash,
)
from studio.jobs.store import Store


def test_snapshot_is_immutable_and_binds_scene_box_to_same_version(tmp_path):
    store = Store(tmp_path)
    job = store.create("generation", {"package_id": "prepared"})
    root = store.directory(job["id"])
    (root / "plans.json").write_text("{}")
    (root / "audit-input.json").write_text("{}")
    scene = [{"elements": [{"box": {"x": 1, "y": 2, "w": 3, "h": 4}}]}]
    for variant in ("executive", "analytical", "story"):
        folder = root / variant
        folder.mkdir()
        (folder / "slides.json").write_text(json.dumps(scene))
        (folder / "deck.pptx").write_bytes(b"deck")
        (folder / "deck.pdf").write_bytes(b"pdf")
        (folder / "deck.html").write_text("<html></html>")
        (folder / "slide-1.png").write_bytes(b"png")
    manifest = {
        "variants": [{"key": key, "slides": 1} for key in ("executive", "analytical", "story")],
        "quality_report": {
            "findings": [
                {
                    "source": "variant",
                    "variant": "executive",
                    "slide": 1,
                    "element": 0,
                    "code": "unhandled",
                    "severity": "warning",
                    "message": "Проверить объект",
                }
            ]
        },
    }
    snapshot = build_snapshot(
        store, job["id"], manifest, SimpleNamespace(id="prepared"), SimpleNamespace(variants=[])
    )
    assert snapshot.findings[0].scene_box.model_dump() == {"x": 1, "y": 2, "w": 3, "h": 4}
    assert snapshot.findings[0].action is None
    digest = save_snapshot(store, snapshot)
    store.update(job["id"], audit_hash=digest)
    assert snapshot_hash(load_snapshot(store, job["id"])) == digest
    with pytest.raises(FileExistsError):
        save_snapshot(store, snapshot)
    (root / "executive/slide-1.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="изменился"):
        load_snapshot(store, job["id"])
