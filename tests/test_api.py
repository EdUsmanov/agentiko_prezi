import time
import asyncio
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from studio.app import create_app
from studio.presentation_service import PresentationService, ApplicationError
from studio.config import Settings
from studio.jobs.store import Store
from studio.contents.uploads import MAX_IMAGE_BYTES


def wait_job(client, jid):
    until = time.monotonic() + 30
    while time.monotonic() < until:
        result = client.get("/api/jobs/" + jid).json()
        if result["state"] not in ("accepted", "running"):
            return result
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_image_reads_follow_validation_and_total_limit(tmp_path, monkeypatch):
    import studio.presentation_service as application_module

    calls = []
    payload = b"x" * MAX_IMAGE_BYTES

    def image(name):
        async def read(limit):
            assert limit == MAX_IMAGE_BYTES + 1
            calls.append(name)
            return payload

        return name, read

    service = PresentationService(
        Settings(data_dir=tmp_path),
        Store(tmp_path),
        SimpleNamespace(prepare=lambda *a, **k: None),
        version_operation=lambda: "fixed",
    )

    async def template_chunks():
        yield b"pptx"

    with pytest.raises(ApplicationError) as invalid:
        asyncio.run(
            service.prepare(
                text="Материал",
                reference_id="also-selected",
                template_name="sample.pptx",
                template_chunks=template_chunks(),
                images=[image("one.png")],
            )
        )
    assert invalid.value.kind == "invalid"
    assert calls == []

    monkeypatch.setattr(
        application_module,
        "sanitize_image",
        lambda raw, name, directory, index: SimpleNamespace(
            name=name, model_dump=lambda: {"name": name}
        ),
    )
    monkeypatch.setattr(application_module, "bind_image_sections", lambda assets, text: assets)
    with pytest.raises(ApplicationError) as too_many:
        asyncio.run(
            service.prepare(
                text="Материал",
                template_name="sample.pptx",
                template_chunks=template_chunks(),
                images=[image(f"{i}.png") for i in range(5)],
            )
        )
    assert too_many.value.kind == "invalid"
    assert calls == ["0.png", "1.png", "2.png", "3.png"]


def test_template_is_analyzed_on_upload_and_reused_for_content(tmp_path, template, content):
    app = create_app(Settings(data_dir=tmp_path / "early"))
    with TestClient(app) as client:
        upload = client.post(
            "/api/templates/analyze",
            files={"template": ("sample.pptx", template.read_bytes())},
        )
        assert upload.status_code == 202, upload.text
        analyzed = wait_job(client, upload.json()["id"])
        assert analyzed["kind"] == "template" and analyzed["state"] == "ready", analyzed
        assert analyzed["template_cache"]["saved"] is True
        assert "auto_generation" not in analyzed
        assert not (app.state.store.directory(analyzed["id"]) / "package.json").exists()
        assert not (app.state.store.directory(analyzed["id"]) / "analysis-input.json").exists()

        prepared = client.post(
            "/api/prepare",
            data={"text": content, "template_job_id": analyzed["id"], "slides": 5},
        )
        assert prepared.status_code == 202, prepared.text
        ready = wait_job(client, prepared.json()["id"])
        assert ready["state"] == "ready", ready
        assert ready["analysis"]["template_cache"]["hit"] is True
        assert ready["content"]["facts"] > 0
        assert (
            app.state.store.directory(ready["id"]) / "input.pptx"
        ).read_bytes() == template.read_bytes()


def test_prepare_waits_for_background_template(tmp_path):
    from studio.jobs.runtime import JobRuntime

    store = Store(tmp_path)
    template = store.create("template")
    preparation = store.create("preparation")
    runtime = JobRuntime(Settings(data_dir=tmp_path), store)

    async def check():
        gate = asyncio.Event()

        async def analyze_template():
            await gate.wait()
            store.update(template["id"], "ready")

        template_task = asyncio.create_task(analyze_template())
        runtime.template_tasks[template["id"]] = template_task
        called = []

        async def supervise(job):
            called.append(job["id"])
            store.update(job["id"], "ready")

        runtime._supervise = supervise
        task = asyncio.create_task(runtime._run_prepare(preparation["id"], template["id"]))
        await asyncio.sleep(0)
        assert called == []
        gate.set()
        await task
        assert called == [preparation["id"]]

    asyncio.run(check())


def test_early_font_wait_does_not_start_generation(tmp_path, monkeypatch):
    from studio.jobs import worker

    store = Store(tmp_path)
    job = store.create("template")
    monkeypatch.setattr(
        worker,
        "preanalyze_template",
        lambda store, jid, settings: store.update(jid, "waiting_fonts", missing_fonts=["missing"]),
    )
    worker.run_job(store, job["id"], Settings(data_dir=tmp_path))
    result = store.get(job["id"])
    assert result["state"] == "waiting_fonts"
    assert "auto_generation" not in result


@pytest.mark.parametrize("extension", ["pptx", "potx", "POTX"])
def test_api_upload_generate_and_xss(tmp_path, template, potx, content, extension):
    app = create_app(Settings(data_dir=tmp_path / "api"))
    content += "\nВидимый текст <img src=x onerror=alert(1)> без выполнения кода."
    with TestClient(app) as client:
        source = template if extension == "pptx" else potx
        response = client.post(
            "/api/prepare",
            data={"text": content, "slides": 5},
            files={
                "template": ("sample." + extension, source.read_bytes(), "application/octet-stream")
            },
        )
        assert response.status_code == 202, response.text
        ready = wait_job(client, response.json()["id"])
        assert ready["state"] == "ready", ready
        assert ready["template"]["name"] == "sample." + extension
        assert (
            tmp_path / "api/jobs" / ready["id"] / "input.pptx"
        ).read_bytes() == source.read_bytes()
        gen = client.post("/api/generate", json={"package_id": ready["id"]})
        assert gen.status_code == 202, gen.text
        done = wait_job(client, gen.json()["id"])
        assert done["state"] == "needs_review", done
        assert done["quality_report"]["errors"] == 0
        from io import BytesIO
        from zipfile import ZipFile
        from pptx import Presentation
        from studio.security import presentation_content_type, PPTX_MAIN

        output = client.get("/api/jobs/" + done["id"] + "/files/executive/deck.pptx").content
        assert len(Presentation(BytesIO(output)).slides) == 5
        with ZipFile(BytesIO(output)) as package:
            assert presentation_content_type(package) == PPTX_MAIN
        preview = client.get("/api/jobs/" + done["id"] + "/files/executive/deck.html")
        assert preview.status_code == 200
        assert "sandbox" in preview.headers["content-security-policy"]
        assert "&lt;img" in preview.text
        assert "<img src=x" not in preview.text
        assert client.get("/api/jobs/" + done["id"] + "/files/input.pptx").status_code == 404
        blocked = client.post(
            "/api/generate",
            json={"package_id": ready["id"]},
            headers={"Origin": "https://evil.example"},
        )
        assert blocked.status_code == 403
    assert app.state.store.get(done["id"])["state"] == "needs_review"


def test_macro_template_extension_rejected(tmp_path, potx, content):
    with TestClient(create_app(Settings(data_dir=tmp_path / "macro"))) as client:
        response = client.post(
            "/api/prepare",
            data={"text": content},
            files={"template": ("sample.potm", potx.read_bytes())},
        )
        assert response.status_code == 422


def test_potx_revision(tmp_path, potx, content):
    with TestClient(create_app(Settings(data_dir=tmp_path / "revision"))) as client:
        response = client.post(
            "/api/prepare",
            data={"text": content, "slides": 5},
            files={"template": ("sample.potx", potx.read_bytes())},
        )
        original = wait_job(client, response.json()["id"])
        assert original["state"] == "ready"
        revision = client.post(
            "/api/packages/" + original["id"] + "/revise",
            json={"instructions": "Сохранить исходные факты", "slides": 5},
        )
        assert revision.status_code == 202
        updated = wait_job(client, revision.json()["id"])
        assert updated["state"] == "ready" and updated["template"]["name"] == "sample.potx"


def test_hard_deadline(tmp_path, template, content):
    app = create_app(Settings(data_dir=tmp_path / "timeout", deadline_seconds=0.01))
    with TestClient(app) as client:
        prep = client.post(
            "/api/prepare",
            data={"text": content, "slides": 5},
            files={"template": ("sample.pptx", template.read_bytes())},
        ).json()
        assert wait_job(client, prep["id"])["state"] == "ready"
        gen = client.post("/api/generate", json={"package_id": prep["id"]}).json()
        assert wait_job(client, gen["id"])["state"] == "timed_out"
        assert client.get("/api/jobs/" + gen["id"] + "/files/presentations.zip").status_code == 409
