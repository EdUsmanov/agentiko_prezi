"""The standalone mock must satisfy the browser's actual API flow."""

from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient
from pptx import Presentation

from studio.api.mock_app import REFERENCE, create_app
from studio.api.frontend import create_frontend_app


def test_mock_frontend_flow_and_downloads():
    backend = create_app(preparation_seconds=0, generation_seconds=0)
    with TestClient(backend) as backend_client:
        assert backend_client.get("/").status_code == 404
        assert backend_client.get("/static/app.js").status_code == 404
    with TestClient(create_frontend_app(backend)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/api/health").json()["mock"] is True
        assert client.get("/api/references").json() == [REFERENCE]

        prepared = client.post(
            "/api/prepare",
            data={
                "reference_id": REFERENCE["id"],
                "text": "# План проекта\nПервый этап\nВторой этап",
                "size_preset": "mini",
            },
        )
        assert prepared.status_code == 202
        pid = prepared.json()["id"]
        package = client.get(f"/api/jobs/{pid}").json()
        assert package["state"] == "ready"
        assert package["analysis"]["planned_slides"] == 4
        assert "mock_text" not in package
        assert client.get(f"/api/jobs/{pid}/files/analysis.json").status_code == 200

        started = client.post("/api/generate", json={"package_id": pid})
        assert started.status_code == 202
        jid = started.json()["id"]
        assert client.post("/api/generate", json={"package_id": pid}).json()["id"] == jid
        done = client.get(f"/api/jobs/{jid}").json()
        assert done["state"] == "completed"
        assert len(done["variants"]) == 3
        assert client.get(f"/api/jobs/{jid}/files/executive/slide-1.png").content.startswith(
            b"\x89PNG"
        )
        deck = client.get(f"/api/jobs/{jid}/files/executive/deck.pptx")
        assert len(Presentation(BytesIO(deck.content)).slides) == 4
        assert client.get(f"/api/jobs/{jid}/files/executive/deck.pdf").content.startswith(b"%PDF")
        assert "План проекта" in client.get(f"/api/jobs/{jid}/files/executive/deck.html").text
        archive = client.get(f"/api/jobs/{jid}/files/presentations.zip")
        with ZipFile(BytesIO(archive.content)) as bundle:
            assert len([name for name in bundle.namelist() if name.endswith(".pptx")]) == 3
        assert client.get(f"/api/jobs/{jid}/files/input.pptx").status_code == 404

        journal = client.get(f"/api/jobs/{jid}/diagnostics").json()
        assert journal["events"]
        assert (
            client.get(f"/api/jobs/{jid}/diagnostics?after={journal['events'][-1]['id']}").json()[
                "events"
            ]
            == []
        )
        assert len(client.get("/api/jobs").json()) == 2

        revision = client.post(
            f"/api/packages/{pid}/revise", json={"instructions": "Сократить", "slides": 3}
        )
        assert revision.status_code == 202
        updated = client.get(f"/api/jobs/{revision.json()['id']}").json()
        assert updated["analysis"]["planned_slides"] == 3
        assert updated["parent_package"] == pid
        assert client.get(f"/api/jobs/{pid}").json()["generation_id"] == jid
        text_revision = client.post(
            f"/api/packages/{pid}/revise", json={"instructions": "Сделай не более 5 слайдов"}
        )
        assert (
            client.get(f"/api/jobs/{text_revision.json()['id']}").json()["analysis"][
                "planned_slides"
            ]
            == 5
        )


def test_mock_rejects_invalid_inputs_and_waits_for_manual_generation():
    with TestClient(create_app(preparation_seconds=0, generation_seconds=0)) as client:
        assert client.post("/api/prepare", data={"text": "Текст"}).status_code == 422
        assert (
            client.post(
                "/api/prepare",
                data={"text": "Текст", "reference_id": "unknown"},
            ).status_code
            == 404
        )
        response = client.post(
            "/api/prepare",
            data={"text": "Текст"},
            files={"template": ("brand.pptx", b"mock bytes")},
        )
        assert response.status_code == 202
        pid = response.json()["id"]
        assert client.get(f"/api/jobs/{pid}").json()["auto_generation"] == "manual"
        cancelled = client.post(f"/api/packages/{pid}/auto-generation/cancel").json()
        assert cancelled["auto_generation"] == "manual"
        assert client.get(f"/api/jobs/{pid}/files/presentations.zip").status_code == 404
        assert (
            client.post("/api/generate", json={"package_id": pid, "variant_count": 2}).status_code
            == 422
        )
        assert (
            client.post("/api/generate", json={"package_id": pid, "variant_count": "1"}).status_code
            == 422
        )
        generation = client.post(
            "/api/generate", json={"package_id": pid, "variant_count": 1}
        ).json()
        done = client.get(f"/api/jobs/{generation['id']}").json()
        assert done["variant_count"] == 1
        assert len(done["variants"]) == 1
        with ZipFile(
            BytesIO(client.get(f"/api/jobs/{generation['id']}/files/presentations.zip").content)
        ) as bundle:
            assert len([name for name in bundle.namelist() if name.endswith(".pptx")]) == 1
        assert (
            client.post("/api/generate", json={"package_id": pid, "variant_count": 3}).status_code
            == 409
        )


def test_mock_new_presentation_reuses_template_with_new_content():
    with TestClient(create_app(preparation_seconds=0, generation_seconds=0)) as client:
        original = client.post(
            "/api/prepare",
            data={"reference_id": REFERENCE["id"], "text": "# Старая тема\nСтарые факты"},
        ).json()
        original = client.get(f"/api/jobs/{original['id']}").json()
        response = client.post(
            "/api/prepare",
            data={"source_package_id": original["id"], "text": "# Новая тема\nНовые факты"},
        )
        assert response.status_code == 202, response.text
        derived = client.get(f"/api/jobs/{response.json()['id']}").json()
        assert derived["id"] != original["id"]
        assert derived["source_package_id"] == original["id"]
        assert derived["template"] == original["template"]
        assert derived["content"]["title"] == "Новая тема"
        assert client.get(f"/api/jobs/{original['id']}").json()["content"]["title"] == "Старая тема"
        generation = client.post("/api/generate", json={"package_id": derived["id"]}).json()
        for variant in client.get(f"/api/jobs/{generation['id']}").json()["variants"]:
            html = client.get(f"/api/jobs/{generation['id']}/files/{variant['key']}/deck.html").text
            assert "Новая тема" in html
            assert any(color in html for color in original["template"]["colors"])
        assert (
            client.post(
                "/api/prepare",
                data={
                    "source_package_id": original["id"],
                    "reference_id": REFERENCE["id"],
                    "text": "Текст",
                },
            ).status_code
            == 422
        )
        assert client.delete(f"/api/jobs/{original['id']}").status_code == 200
        assert client.get(f"/api/jobs/{derived['id']}").json()["state"] == "ready"
