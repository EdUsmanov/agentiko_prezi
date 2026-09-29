from fastapi.testclient import TestClient

from studio.app import create_app
from studio.config import Settings
from studio.api.mock_app import REFERENCE, create_app as create_mock_app


def test_production_delete_removes_package_generation_revisions_and_files(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data"))
    store = app.state.store
    package = store.create("preparation")
    pid = package["id"]
    store.update(pid, "ready", auto_generation="cancelled")
    generation = store.create("generation", {"package_id": pid})
    gid = generation["id"]
    store.update(gid, "completed")
    revision = store.create("preparation", {"parent_package": pid})
    rid = revision["id"]
    store.update(rid, "ready")
    for key in (pid, gid, rid):
        (store.directory(key) / "artifact.txt").write_text("test")

    with TestClient(app) as client:
        response = client.delete(f"/api/jobs/{pid}")
        assert response.status_code == 200, response.text
        assert set(response.json()["deleted"]) == {pid, gid, rid}
        assert client.get("/api/jobs").json() == []
        for key in (pid, gid, rid):
            assert client.get(f"/api/jobs/{key}").status_code == 404
            assert not store.directory(key).exists()


def test_mock_delete_removes_generated_files_and_rejects_active_job():
    with TestClient(create_mock_app(preparation_seconds=0, generation_seconds=0)) as client:
        package = client.post(
            "/api/prepare",
            data={"reference_id": REFERENCE["id"], "text": "Тест удаления"},
        ).json()
        pid = package["id"]
        assert client.delete(f"/api/jobs/{pid}").status_code == 409
        assert client.get(f"/api/jobs/{pid}").json()["state"] == "ready"
        gid = client.post("/api/generate", json={"package_id": pid}).json()["id"]
        assert client.get(f"/api/jobs/{gid}").json()["state"] == "completed"
        assert client.get(f"/api/jobs/{gid}/files/presentations.zip").status_code == 200
        deleted = client.delete(f"/api/jobs/{pid}")
        assert deleted.status_code == 200, deleted.text
        assert set(deleted.json()["deleted"]) == {pid, gid}
        assert client.get("/api/jobs").json() == []
        assert client.get(f"/api/jobs/{gid}/files/presentations.zip").status_code == 404


def test_delete_generation_removes_selected_repair_descendants(tmp_path):
    from studio.jobs.store import Store

    store = Store(tmp_path)
    package = store.create("preparation")
    store.update(package["id"], "ready")
    parent = store.create("generation", {"package_id": package["id"]})
    store.update(parent["id"], "completed")
    child = store.create(
        "generation", {"package_id": package["id"], "parent_generation_id": parent["id"]}
    )
    store.update(child["id"], "completed")
    assert set(store.delete_tree(parent["id"])["deleted"]) == {parent["id"], child["id"]}
    assert store.get(package["id"])["state"] == "ready"


def test_deleted_scheduled_package_cannot_start_or_receive_late_events(tmp_path, monkeypatch):
    import asyncio
    import time
    from studio.jobs.runtime import JobRuntime
    from studio.jobs.store import Store

    store = Store(tmp_path)
    job = store.create("preparation")
    store.update(job["id"], "ready", auto_generation="scheduled", auto_generate_at=time.time())
    runtime = JobRuntime(Settings(data_dir=tmp_path), store)
    started = []
    runtime.start_generation = lambda *args, **kwargs: started.append(args)

    async def delete_while_waiting(_):
        store.delete_tree(job["id"])

    monkeypatch.setattr("studio.jobs.runtime.asyncio.sleep", delete_while_waiting)
    asyncio.run(runtime.auto_generate(job["id"]))
    assert not started and not store.events(job["id"]) and not store.recent()
