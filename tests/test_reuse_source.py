from importlib import import_module
from types import SimpleNamespace

from fastapi.testclient import TestClient

from studio.config import Settings


def test_production_prepare_copies_stored_template_for_new_text(tmp_path, monkeypatch):
    app_module = import_module("studio.app")
    app = app_module.create_app(Settings(data_dir=tmp_path / "data"))
    store = app.state.store
    original = store.create("preparation", {"template_name": "brand.pptx"})
    source_id = original["id"]
    source_bytes = b"stored-template"
    (store.directory(source_id) / "input.pptx").write_bytes(source_bytes)
    store.update(source_id, "ready", auto_generation="cancelled")
    monkeypatch.setattr(
        app_module,
        "load_package",
        lambda current_store, pid: SimpleNamespace(template=SimpleNamespace(name="brand.pptx")),
    )
    analyzed = []

    def fake_prepare(current_store, jid, text, audience, instructions, slides, *args, **kwargs):
        analyzed.append((jid, text))
        current_store.update(
            jid,
            "ready",
            phase="Готово",
            auto_generation="needs_confirmation",
            analysis={"slide_budget": {"status": "ok"}},
        )

    monkeypatch.setattr(app_module, "prepare", fake_prepare)
    with TestClient(app) as client:
        response = client.post(
            "/api/prepare",
            data={"source_package_id": source_id, "text": "Новый материал"},
        )
        assert response.status_code == 202, response.text
        new_id = response.json()["id"]
        assert new_id != source_id
        assert store.get(new_id)["source_package_id"] == source_id
        assert (store.directory(new_id) / "input.pptx").read_bytes() == source_bytes
        assert analyzed == [(new_id, "Новый материал")]
        assert store.get(source_id)["state"] == "ready"
