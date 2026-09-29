"""Organizer demos must never become an analysis/readiness dependency."""

import asyncio
import json
import shutil
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from studio import app as app_module, cache_version, pipeline
from studio.config import Settings
from studio.examples import index_examples, sources
from studio.gateway import ModelGateway
from studio.store import Store


def seed(settings):
    rows = [{"id": f"{i:020x}", "name": f"Demo {i}.pptx"} for i in range(3)]
    for row in rows:
        path = settings.data_dir / "references" / row["id"] / "input.pptx"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(row["name"].encode())
    cache_version.atomic_json(settings.data_dir / "references/index.json", rows)
    # A failed, corrupt historical library must have no effect on new jobs.
    directory = settings.data_dir / "references/library"
    directory.mkdir()
    (directory / "active.json").write_text("{broken")
    cache_version.atomic_json(directory / "attempt.json", {"status": "failed"})
    return rows


def forbidden(*args, **kwargs):
    pytest.fail("Optional demos triggered analysis, rendering or a model call")


def test_registration_only_copies_raw_files(tmp_path, template, monkeypatch):
    settings = Settings(data_dir=tmp_path / "data")
    monkeypatch.setattr(pipeline, "analyze_template", forbidden)
    monkeypatch.setattr(ModelGateway, "json_request", forbidden)
    rows = index_examples([template, template], settings)
    assert len(rows) == 1
    assert set(rows[0]) == {"id", "name"}
    folder = settings.data_dir / "references" / rows[0]["id"]
    assert sorted(p.name for p in folder.iterdir()) == ["input.pptx"]
    assert (folder / "input.pptx").read_bytes() == template.read_bytes()
    assert sources(settings) == rows


def test_startup_never_builds_or_reads_old_library(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    rows = seed(settings)
    before = {p: p.read_bytes() for p in (tmp_path / "references").rglob("*") if p.is_file()}
    monkeypatch.setattr(ModelGateway, "json_request", forbidden)
    monkeypatch.setattr(pipeline, "analyze_template", forbidden)
    monkeypatch.setattr(app_module.asyncio, "create_subprocess_exec", forbidden)
    app = app_module.create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/references").json() == rows
        assert client.get("/api/health").json()["features"]["organizer_preanalysis"] is True
        assert client.get("/api/runtime").json() == {
            "restart_required": False,
            "organizer_preanalysis": True,
        }
        assert client.post("/api/reference-library/rebuild").status_code == 404
        assert client.get("/api/reference-library").status_code == 404
        assert client.get("/api/library/diagnostics").status_code == 404
        assert "library-rebuild" not in client.get("/").text
        assert app.state.store.events("library") == []
    assert {
        p: p.read_bytes() for p in (tmp_path / "references").rglob("*") if p.is_file()
    } == before


@pytest.mark.parametrize("demo", [False, True])
def test_prepare_uses_only_explicitly_selected_input(tmp_path, monkeypatch, demo):
    settings = Settings(data_dir=tmp_path)
    rows = seed(settings)
    seen = []

    def prepare(store, jid, *args):
        seen.append((store.directory(jid) / "input.pptx").read_bytes())
        store.update(jid, "failed", error="Test stopped before real analysis")

    monkeypatch.setattr(app_module, "prepare", prepare)
    monkeypatch.setattr(ModelGateway, "json_request", forbidden)
    with TestClient(app_module.create_app(settings)) as client:
        data = {"text": "Материал пользователя"}
        files = None
        if demo:
            data["reference_id"] = rows[1]["id"]
        else:
            files = {"template": ("new-user-template.pptx", b"new user input")}
        response = client.post("/api/prepare", data=data, files=files)
        assert response.status_code == 202, response.text
        for _ in range(100):
            if seen:
                break
            time.sleep(0.01)
    assert seen == [rows[1]["name"].encode() if demo else b"new user input"]


def test_corrupt_optional_index_does_not_block_upload(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    seed(settings)
    (tmp_path / "references/index.json").write_text("{broken")
    monkeypatch.setattr(
        app_module, "prepare", lambda store, jid, *args: store.update(jid, "failed")
    )
    with TestClient(app_module.create_app(settings)) as client:
        assert client.get("/api/references").json() == []
        response = client.post(
            "/api/prepare",
            data={"text": "Материал"},
            files={"template": ("user.pptx", b"user input")},
        )
        assert response.status_code == 202


def test_demo_index_cannot_escape_root(tmp_path):
    settings = Settings(data_dir=tmp_path)
    cache_version.atomic_json(
        tmp_path / "references/index.json",
        [{"id": "../outside", "name": "outside"}, None, {"id": 123}],
    )
    assert sources(settings) == []


def test_runtime_version_guard_is_preserved(tmp_path, monkeypatch):
    version = ["loaded"]
    monkeypatch.setattr(app_module, "pipeline_version", lambda: version[0])
    with TestClient(app_module.create_app(Settings(data_dir=tmp_path))) as client:
        version[0] = "changed"
        assert client.get("/api/runtime").json()["restart_required"] is True
        response = client.post(
            "/api/prepare",
            data={"text": "Материал"},
            files={"template": ("user.pptx", b"user input")},
        )
        assert response.status_code == 503
        assert "Перезапустите" in response.json()["detail"]


def test_user_analysis_completes_with_broken_organizer_library(
    tmp_path, template, content, monkeypatch
):
    import studio.template_analysis as analysis

    settings = Settings(data_dir=tmp_path / "data")
    seed(settings)
    store = Store(settings.data_dir)
    job = store.create("preparation", {"template_name": template.name})
    shutil.copyfile(template, store.directory(job["id"]) / "input.pptx")
    seen = []
    original = analysis.analyze_meaning

    async def record(inventory, *args, **kwargs):
        seen.append(inventory)
        return await original(inventory, *args, **kwargs)

    monkeypatch.setattr(analysis, "analyze_meaning", record)
    monkeypatch.setattr(ModelGateway, "json_request", forbidden)
    pipeline.prepare(store, job["id"], content, "Команда", "", 5, settings)
    assert store.get(job["id"])["state"] == "ready", store.get(job["id"])
    result = pipeline.load_package(store, job["id"])
    assert len(seen) == 1
    assert result.template.name == template.name
    assert (
        not {"references", "reference_knowledge", "reference_library_version"}
        & result.analysis.keys()
    )


def test_planner_does_not_send_historical_organizer_knowledge(prepared):
    from studio.planner import plan, extractive_plans

    _, _, package = prepared
    package.analysis["reference_knowledge"] = [{"private_organizer_marker": "must not leak"}]
    seen = []

    async def request(stage, payload, **kwargs):
        seen.append(payload)
        return extractive_plans(package).model_dump()

    gateway = SimpleNamespace(settings=SimpleNamespace(mode="api"), json_request=request, calls=[])
    asyncio.run(plan(package, gateway, 30))
    assert seen
    assert all("reference_knowledge" not in payload for payload in seen)
    assert "private_organizer_marker" not in json.dumps(seen)
    assert "template_analysis" in seen[0]


def test_pipeline_hash_covers_adapter(monkeypatch, tmp_path):
    (tmp_path / "studio").mkdir()
    (tmp_path / "scripts/pptagent_runtime").mkdir(parents=True)
    (tmp_path / "scripts/pptagent_runtime/Dockerfile").write_text("test")
    for name in ("requirements.lock", "pyproject.toml"):
        (tmp_path / name).write_text("test")
    module = tmp_path / "studio/colors.py"
    module.write_text("version1")
    monkeypatch.setattr(cache_version, "ROOT", tmp_path)
    before = cache_version.pipeline_version()
    module.write_text("version2")
    assert cache_version.pipeline_version() != before


def test_reference_can_recommend_one_variant_without_filename_rules(tmp_path, template):
    settings = Settings(data_dir=tmp_path / "data")
    rows = index_examples([template], settings, single_variant_paths=[template])
    assert rows[0]["variant_count"] == 1
    assert sources(settings) == rows
