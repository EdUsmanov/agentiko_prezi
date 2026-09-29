"""HTTP contracts for explicit approval and selected audit repair."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from studio.app import create_app
from studio.config import Settings
from studio.checks import review_snapshot
from studio.models import BriefDraft, Constraints, ContentModel, DraftBullet, DraftSlide, Fact


def test_quality_failed_revision_exposes_only_audited_preview_and_claims_once(
    tmp_path, monkeypatch
):
    app = create_app(Settings(data_dir=tmp_path))
    audit_hash = "a" * 64
    finding = SimpleNamespace(id="finding-1", action="change_layout")
    snapshot = SimpleNamespace(
        findings=[finding],
        files={"executive/slide-1.png": "saved-hash"},
        model_dump=lambda **kwargs: {
            "generation_id": source_id,
            "findings": [{"id": "finding-1", "action": "change_layout"}],
        },
    )
    monkeypatch.setattr(review_snapshot, "load_snapshot", lambda store, gid: snapshot)
    monkeypatch.setattr(review_snapshot, "snapshot_hash", lambda value: audit_hash)
    launched = []
    app.state.runtime.supervise = lambda job: launched.append(job["id"])

    with TestClient(app) as client:
        source = app.state.store.create("generation", {"package_id": "b" * 32})
        source_id = source["id"]
        root = app.state.store.directory(source_id)
        (root / "executive").mkdir()
        (root / "executive/slide-1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (root / "executive/deck.pptx").write_bytes(b"private draft")
        app.state.store.update(
            source_id,
            "failed",
            review_available=True,
            failure_kind="quality_gate",
            audit_hash=audit_hash,
        )

        found = client.get(f"/api/generations/{source_id}/findings")
        assert found.status_code == 200
        assert found.json()["audit_hash"] == audit_hash
        assert client.get(f"/api/generations/{source_id}/preview/executive/1").status_code == 200
        assert client.get(f"/api/generations/{source_id}/preview/executive/2").status_code == 404
        assert client.get(f"/api/jobs/{source_id}/files/executive/deck.pptx").status_code == 409

        payload = {"audit_hash": audit_hash, "finding_ids": ["finding-1"]}
        first = client.post(f"/api/generations/{source_id}/repair", json=payload)
        second = client.post(f"/api/generations/{source_id}/repair", json=payload)
        assert first.status_code == second.status_code == 202
        assert first.json()["id"] == second.json()["id"]
        assert launched == [first.json()["id"]]
        assert first.json()["operation"] == "repair"
        assert (
            client.post(
                f"/api/generations/{source_id}/repair",
                json={"audit_hash": audit_hash, "finding_ids": ["unknown"]},
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/generations/{source_id}/repair",
                json={"audit_hash": "c" * 64, "finding_ids": ["finding-1"]},
            ).status_code
            == 409
        )


def test_generic_failure_never_exposes_review_or_preview(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        source = app.state.store.create("generation", {"package_id": "b" * 32})
        app.state.store.update(source["id"], "failed", error="network failed")
        gid = source["id"]
        assert client.get(f"/api/generations/{gid}/findings").status_code == 409
        assert client.get(f"/api/generations/{gid}/preview/executive/1").status_code == 409
        assert (
            client.post(
                f"/api/generations/{gid}/repair",
                json={"audit_hash": "a" * 64, "finding_ids": ["anything"]},
            ).status_code
            == 409
        )


def test_approval_binds_both_hashes_and_cancel_is_terminal(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path))
    monkeypatch.setattr(
        app.state.presentation_service,
        "load_operation",
        lambda *_: SimpleNamespace(
            input_mode="brief",
            draft=SimpleNamespace(),
        ),
    )
    monkeypatch.setattr(
        "studio.presentation_service.assert_draft_matches_package", lambda package: None
    )
    monkeypatch.setattr("studio.presentation_service.hash_draft", lambda draft: "d" * 64)
    scheduled = []
    app.state.runtime.schedule_auto_generation = scheduled.append
    with TestClient(app) as client:
        job = app.state.store.create(
            "preparation",
            {
                "input_mode": "brief",
                "package_hash": "p" * 64,
                "draft_hash": "d" * 64,
                "control": {"slide_budget": {"status": "exact"}},
            },
        )
        app.state.store.update(job["id"], "ready")
        path = f"/api/packages/{job['id']}/approve"
        assert (
            client.post(path, json={"package_hash": "x" * 64, "draft_hash": "d" * 64}).status_code
            == 409
        )
        body = {"package_hash": "p" * 64, "draft_hash": "d" * 64}
        assert client.post(path, json=body).status_code == 200
        assert client.post(path, json=body).status_code == 200
        assert app.state.store.get(job["id"])["approved_draft_hash"] == "d" * 64
        assert scheduled == [job["id"], job["id"]]

        active = app.state.store.create("template")
        response = client.post(f"/api/jobs/{active['id']}/cancel")
        assert response.status_code == 200
        assert response.json()["state"] == "cancelled"
        assert client.post(f"/api/jobs/{active['id']}/cancel").status_code == 409


def test_draft_edit_creates_new_unapproved_preparation_with_persisted_input(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path))
    source_content = ContentModel(title="Тема", facts=[Fact(id="f1", text="Исходный факт")])
    package = SimpleNamespace(
        input_mode="brief",
        draft=BriefDraft(
            slides=[
                DraftSlide(
                    title="Тема", bullets=[DraftBullet(text="Исходный факт", fact_ids=["f1"])]
                )
            ]
        ),
        original_content=source_content,
        brief_evidence=source_content,
        constraints=Constraints(slides=1),
    )
    monkeypatch.setattr(app.state.presentation_service, "load_operation", lambda *_: package)
    monkeypatch.setattr(
        "studio.presentation_service.assert_draft_matches_package", lambda package: None
    )
    started = []
    app.state.runtime.prepare = lambda jid: started.append(jid)
    with TestClient(app) as client:
        original = app.state.store.create(
            "preparation",
            {
                "package_hash": "p" * 64,
                "input_mode": "brief",
                "template_name": "x.pptx",
            },
        )
        app.state.store.update(original["id"], "ready")
        root = app.state.store.directory(original["id"])
        (root / "input.pptx").write_bytes(b"template")
        (root / "images.json").write_text("[]")
        body = {
            "package_hash": "p" * 64,
            "draft": {
                "slides": [
                    {
                        "title": "Тема",
                        "purpose": "auto",
                        "bullets": [{"text": "Исправленный факт", "fact_ids": ["f1"]}],
                    }
                ]
            },
        }
        first = client.post(f"/api/packages/{original['id']}/draft", json=body)
        second = client.post(f"/api/packages/{original['id']}/draft", json=body)
        assert first.status_code == second.status_code == 202
        new_id = first.json()["id"]
        assert second.json()["id"] == new_id
        assert started == [new_id]
        assert app.state.store.get(original["id"])["state"] == "ready"
        assert app.state.store.get(new_id).get("approved_draft_hash") is None
        new_root = app.state.store.directory(new_id)
        assert (new_root / "input.pptx").read_bytes() == b"template"
        import json

        request = json.loads((new_root / "request.json").read_text())
        assert request["input_mode"] == "brief"
        assert request["draft"]["slides"][0]["bullets"][0]["text"] == "Исправленный факт"
