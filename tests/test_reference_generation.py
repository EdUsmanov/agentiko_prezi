"""Saved template readiness must never authorize content or extra exports."""

import asyncio
import json
import time
from dataclasses import replace
from zipfile import ZipFile
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from studio.app import GenerateRequest, create_app
from studio.config import Settings
from studio.examples import index_examples
from studio.pipeline import generate
from studio.template_cache import TemplateCache
from studio.reference_analysis import reference_profile


@pytest.mark.parametrize("value", [0, 2, 4, True, "1", 1.0, None])
def test_variant_request_rejects_ambiguous_count(value):
    with pytest.raises(ValidationError):
        GenerateRequest(package_id="package", variant_count=value)


def test_single_variant_is_exported_and_audited_without_sibling_files(prepared):
    settings, store, package = prepared
    before = (store.directory(package.id) / "package.json").read_bytes()
    assert store.get(package.id)["auto_generation"] == "manual"
    assert not store.scheduled()
    job, created = store.generation_for(package.id, variant_count=1)
    assert created
    store.update(job["id"], deadline_at=time.time() + 300)
    asyncio.run(generate(store, job["id"], settings))
    done = store.get(job["id"])
    assert done["state"] in ("completed", "needs_review"), done
    assert done["errors"] == 0
    assert [v["key"] for v in done["variants"]] == ["executive"]
    assert done["composition_diversity"]["verified"] is True
    assert not any(
        f["code"] == "insufficient_diversity" for f in done["quality_report"]["findings"]
    )
    assert (store.directory(package.id) / "package.json").read_bytes() == before
    root = store.directory(job["id"])
    assert not (root / "analytical").exists() and not (root / "story").exists()
    with ZipFile(root / "presentations.zip") as z:
        assert [n for n in z.namelist() if n.endswith(".pptx")] == ["executive/deck.pptx"]
    assert store.generation_for(package.id, variant_count=1) == (done, False)
    with pytest.raises(ValueError, match="другое количество"):
        store.generation_for(package.id, variant_count=3)


def test_reference_profile_is_read_only_private_and_invalidated(prepared):
    settings, store, package = prepared
    source = store.directory(package.id) / "input.pptx"
    row = index_examples([source], settings)[0]
    cache = TemplateCache(settings)
    assert cache.save(package.template, source.parent, package.analysis)
    before = {p: p.stat().st_mtime_ns for p in settings.data_dir.rglob("*") if p.is_file()}
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/references/{row['id']}/profile")
        assert response.status_code == 200
        report = response.json()
        assert report["status"] == "technical_only"
        assert report["template"]["sha256"] == package.template.sha256
        assert str(settings.data_dir) not in response.text
        assert "@template/" not in response.text
        assert "facts" not in report and "package_id" not in report
        assert client.get("/api/references/unknown/profile").status_code == 404
    # App lifespan can recover jobs, but profile lookup must not materialize a new analysis.
    assert set(before) == {p for p in settings.data_dir.rglob("*") if p.is_file()}
    assert (
        reference_profile(replace(settings, model_id="changed"), row["id"])["status"] == "not_ready"
    )
    entry = cache.location(package.template.sha256)
    snapshot = json.loads((entry / "snapshot.json").read_text())
    (entry / "files" / next(iter(snapshot["files"]))).write_bytes(b"corrupt")
    assert reference_profile(settings, row["id"])["status"] == "not_ready"


def test_production_generation_deadline_is_five_minutes():
    assert Settings(data_dir=None).deadline_seconds == 300


def test_single_variant_deeppresenter_preserves_full_coverage(prepared):
    from studio.deeppresenter import CompositionEnvironment, Assignment
    from studio.planner import extractive_plans
    from studio.models import Plans

    _, _, package = prepared
    full = extractive_plans(package)
    selected = Plans(variants=[full.variants[0].model_copy(deep=True)])
    environment = CompositionEnvironment(package, selected)
    result = environment.compose([Assignment(**a) for a in environment.baseline_assignments])
    assert result["accepted"] is True
    report = environment.inspect()
    assert list(report["findings"]) == ["executive"]
    assert len(environment.expected) == len(full.variants[0].slides)
    assert len(full.variants) == 3
