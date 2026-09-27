import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
import time

from fastapi.testclient import TestClient
from PIL import Image

from studio.config import Settings
from studio.store import Store
from studio.diagnostics import configure, scope, event, capture_stream
from studio.app import create_app
from studio import app as app_module
from studio import cache_version as library


def test_unlimited_is_default(tmp_path):
    settings = Settings(data_dir=tmp_path)
    assert settings.deadline_seconds is None


def test_server_code_not_analysis_fingerprint(tmp_path, monkeypatch):
    (tmp_path / "studio").mkdir()
    (tmp_path / "studio/app.py").write_text("server v1")
    (tmp_path / "studio/template.py").write_text("analysis v1")
    monkeypatch.setattr(library, "ROOT", tmp_path)
    first = library.analysis_version()
    (tmp_path / "studio/app.py").write_text("server v2")
    assert library.analysis_version() == first
    (tmp_path / "studio/template.py").write_text("analysis v2")
    assert library.analysis_version() != first


def test_generation_claim_is_atomic_and_cancelled_auto_is_ignored(tmp_path):
    store = Store(tmp_path)
    package = store.create("preparation")
    store.update(package["id"], "ready", auto_generation="scheduled")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.generation_for(package["id"]), range(4)))
    assert len({job["id"] for job, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    other = store.create("preparation")
    store.update(other["id"], "ready", auto_generation="cancelled")
    assert store.generation_for(other["id"], automatic=True) == (None, False)


def test_cancel_and_autostart_share_atomic_boundary(tmp_path):
    store = Store(tmp_path)
    for _ in range(12):
        package = store.create("preparation")
        store.update(package["id"], "ready", auto_generation="scheduled")
        with ThreadPoolExecutor(max_workers=2) as pool:
            cancel = pool.submit(store.cancel_auto_generation, package["id"])
            launch = pool.submit(store.generation_for, package["id"], True)
            cancel.result()
            job, created = launch.result()
        final = store.get(package["id"])
        if created:
            assert final["auto_generation"] == "started"
            assert final["generation_id"] == job["id"]
            store.update(job["id"], "completed")
        else:
            assert final["auto_generation"] == "cancelled"
            assert not final.get("generation_id")


def test_prepare_callback_does_not_rearm_cancelled_countdown(tmp_path, monkeypatch):
    def prepared(store, jid, *args):
        store.update(
            jid,
            "ready",
            analysis={},
            auto_generation="scheduled",
            auto_generate_at=time.time() + 60,
        )
        store.cancel_auto_generation(jid)

    monkeypatch.setattr(app_module, "prepare", prepared)
    application = create_app(Settings(data_dir=tmp_path))
    with TestClient(application) as client:
        response = client.post(
            "/api/prepare", data={"text": "Материал"}, files={"template": ("test.pptx", b"fake")}
        )
        jid = response.json()["id"]
        for _ in range(100):
            job = client.get("/api/jobs/" + jid).json()
            if job.get("auto_generation") == "cancelled":
                break
            time.sleep(0.01)
        time.sleep(0.05)
        assert client.get("/api/jobs/" + jid).json()["auto_generation"] == "cancelled"


def test_redaction_nested_and_pipe_boundaries(tmp_path):
    store = Store(tmp_path)
    job = store.create("preparation")
    configure("super-secret-test-key")
    with scope(store, job["id"]):
        event("test", nested={"Authorization": "Bearer super-secret-test-key"})

    class Pipe:
        chunks = iter([b"key super-secret-", b"test-key\n", b"Bearer another-secret\n", b""])

        async def read(self, size):
            return next(self.chunks)

    asyncio.run(capture_stream(store, job["id"], Pipe()))
    raw = json.dumps(store.events(job["id"]))
    assert "super-secret" not in raw and "another-secret" not in raw
    assert "[REDACTED]" in raw


def test_job_journal_is_available_for_failed_job(tmp_path):
    application = create_app(Settings(data_dir=tmp_path))
    job = application.state.store.create("preparation")
    configure("journal-secret-test-key")
    application.state.store.update(
        job["id"],
        "failed",
        error="<script>bad()</script>",
        diagnostics=[{"severity": "error", "message": "Detailed diagnostic"}],
        warnings=["Warning journal-secret-test-key"],
        quality_report={"errors": 1, "findings": [{"message": "Geometry issue"}]},
        variants=[{"key": "a", "findings": [{"message": "Variant issue"}]}],
    )
    with TestClient(application) as client:
        report = client.get("/api/jobs/" + job["id"] + "/diagnostics")
        assert report.status_code == 200 and report.json()["events"][-1]["level"] == "error"
        checks = report.json()["checks"]
        assert checks["diagnostics"][0]["message"] == "Detailed diagnostic"
        assert checks["quality_report"]["findings"][0]["message"] == "Geometry issue"
        assert checks["variants"][0]["findings"][0]["message"] == "Variant issue"
        assert checks["warnings"] == ["Warning [REDACTED]"]
        assert (
            client.get("/api/jobs/" + job["id"] + "/diagnostics?download=true").json()["checks"]
            == checks
        )
        assert (
            client.get("/api/jobs/" + job["id"] + "/diagnostics?download=true")
            .headers["content-disposition"]
            .startswith("attachment")
        )
        assert client.get("/api/jobs/not-a-job/diagnostics").status_code == 404


def test_job_journal_shows_latest_events_and_download_keeps_history(tmp_path):
    application = create_app(Settings(data_dir=tmp_path))
    with TestClient(application) as client:
        jid = application.state.store.create("preparation")["id"]
        with application.state.store.connect() as db:
            db.executemany(
                "INSERT INTO events(job_id,created,level,event,data) VALUES(?,?,?,?,?)",
                [
                    (jid, time.time(), "info", "test", json.dumps({"sequence": i}))
                    for i in range(510)
                ],
            )
        events = client.get("/api/jobs/" + jid + "/diagnostics").json()["events"]
        assert len(events) == 500
        assert events[0]["data"]["sequence"] == 10 and events[-1]["data"]["sequence"] == 509
        assert (
            len(client.get("/api/jobs/" + jid + "/diagnostics?download=true").json()["events"])
            == 511
        )


def test_preparation_schedules_sixty_seconds_and_cancel(tmp_path, monkeypatch):
    def prepared(store, jid, *args):
        store.update(
            jid,
            "ready",
            analysis={},
            template={},
            content={},
            constraints={},
            auto_generation="scheduled",
            auto_generate_at=time.time() + 60,
        )

    monkeypatch.setattr(app_module, "prepare", prepared)
    application = create_app(Settings(data_dir=tmp_path))
    # Existing API does upload validation in prepare; this test isolates scheduling.
    with TestClient(application) as client:
        before = time.time()
        response = client.post(
            "/api/prepare", data={"text": "Материал"}, files={"template": ("test.pptx", b"fake")}
        )
        jid = response.json()["id"]
        for _ in range(100):
            job = client.get("/api/jobs/" + jid).json()
            if job.get("auto_generation"):
                break
            time.sleep(0.01)
        assert job["auto_generation"] == "scheduled"
        assert before + 60 <= job["auto_generate_at"] <= time.time() + 60
        cancelled = client.post("/api/packages/" + jid + "/auto-generation/cancel").json()
        assert cancelled["auto_generation"] == "cancelled"


def test_restart_resumes_due_autostart_once(tmp_path, monkeypatch):
    application = create_app(Settings(data_dir=tmp_path))
    store = application.state.store
    package = store.create("preparation")
    store.update(
        package["id"], "ready", auto_generation="scheduled", auto_generate_at=time.time() - 1
    )
    monkeypatch.setattr(
        app_module,
        "load_package",
        lambda *a: SimpleNamespace(
            analysis={}, constraints=SimpleNamespace(confirm_plan=False, slides=5)
        ),
    )
    calls = []

    class Pipe:
        async def read(self, n):
            return b""

    class Process:
        pid = 999999
        returncode = 0
        stdout = Pipe()

        async def wait(self):
            return 0

    async def spawn(*args, **kwargs):
        calls.append(args)
        jid = args[3]
        store.update(jid, "completed")
        return Process()

    monkeypatch.setattr(app_module.asyncio, "create_subprocess_exec", spawn)
    with TestClient(application) as client:
        for _ in range(100):
            job = client.get("/api/jobs/" + package["id"]).json()
            if job.get("generation_id"):
                break
            time.sleep(0.01)
        assert job["auto_generation"] == "started"
        manual = client.post("/api/generate", json={"package_id": package["id"]}).json()
        assert manual["id"] == job["generation_id"]
        assert len(calls) == 1


def test_generation_can_finish_after_five_minutes(prepared):
    from studio.pipeline import generate

    settings, store, package = prepared
    assert settings.deadline_seconds is None
    job = store.create("generation", {"package_id": package.id, "deadline_at": None})
    with store.connect() as c:
        c.execute("UPDATE jobs SET created=? WHERE id=?", (time.time() - 360, job["id"]))
    asyncio.run(generate(store, job["id"], settings))
    done = store.get(job["id"])
    assert done["state"] in ("completed", "needs_review"), done
    assert done["elapsed_seconds"] > 360
    assert len(done["variants"]) == 3


def test_failed_generation_allows_explicit_retry_but_not_paid_auto_retry(tmp_path):
    store = Store(tmp_path)
    package = store.create("preparation")
    store.update(package["id"], "ready", auto_generation="scheduled")
    first, _ = store.generation_for(package["id"])
    store.update(first["id"], "failed")
    automatic, created = store.generation_for(package["id"], automatic=True)
    assert automatic["id"] == first["id"] and not created
    retry, created = store.generation_for(package["id"])
    assert retry["id"] != first["id"] and created


def test_zone_coordinates_match_protected_regions(monkeypatch):
    from studio import portable_templates as portable

    seen = {}

    def analyze(image, slide, vl_cells=None):
        assert vl_cells is None
        seen.update(size=image.size, slide=slide)
        return {"box": None, "reason": "conservative"}

    monkeypatch.setattr(portable, "analyze_image", analyze)
    pattern = SimpleNamespace(source_slide=1, safe_text_zone=None)
    profile = SimpleNamespace(width=960, height=540)
    slide = {"protectedRegions": [{"x": 1000, "y": 200, "width": 100, "height": 100}]}
    result = portable.inspect_text_zone(
        Image.new("RGB", (1440, 810)), pattern, profile, {"slides": [slide]}
    )
    assert seen["size"] == (1280, 720) and seen["slide"] is slide
    assert result["box"] is None and pattern.safe_text_zone is result


def test_peer_background_does_not_modify_input_and_fields(template, tmp_path):
    from studio.template import analyze_template
    from studio.portable_templates import extract_backgrounds, clean_editable_source
    from studio.powerpoint import open_presentation

    source = template.read_bytes()
    output = tmp_path / "peer"
    output.mkdir()
    profile = analyze_template(template, output)
    model = extract_backgrounds(profile, template, output)
    assert model["schemaVersion"] == 1
    assert template.read_bytes() == source
    assert (
        open_presentation(profile.background_source).slide_width
        == open_presentation(template).slide_width
    )
    prs = open_presentation(template)
    clean_editable_source(prs, profile.patterns)
    for pattern in profile.patterns:
        surface = (
            prs.slides[pattern.source_slide - 1]
            if pattern.source_slide
            else prs.slide_masters[pattern.master_index].slide_layouts[pattern.layout_index]
        )
        assert {f["shape_id"] for f in pattern.fields} <= {s.shape_id for s in surface.shapes}


def test_blank_svg_card_is_distinguished_from_source_illustration(tmp_path):
    from zipfile import ZipFile
    from studio.portable_templates import _solid_svg_panel

    path = tmp_path / "panels.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "white.svg",
            '<svg xmlns="http://www.w3.org/2000/svg" width="858" height="338">'
            '<rect width="858" height="338" rx="40" fill="white"/></svg>',
        )
        archive.writestr(
            "illustration.svg",
            '<svg xmlns="http://www.w3.org/2000/svg" width="858" height="338">'
            '<rect width="858" height="338" fill="white"/>'
            '<path d="M0 0L10 10"/></svg>',
        )
        archive.writestr(
            "script.svg",
            '<svg xmlns="http://www.w3.org/2000/svg" width="858" height="338" onload="alert(1)">'
            '<rect width="858" height="338" fill="white"/></svg>',
        )
    with ZipFile(path) as archive:
        assert _solid_svg_panel(archive, "white.svg")
        assert not _solid_svg_panel(archive, "illustration.svg")
        assert not _solid_svg_panel(archive, "script.svg")
