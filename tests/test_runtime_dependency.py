"""A dependent preparation keeps template failure details in its own journal."""

import asyncio

from studio.config import Settings
from studio.jobs.runtime import JobRuntime
from studio.jobs.store import Store
from studio.presentation_service import PresentationService


def test_failed_template_dependency_links_redacted_model_diagnostic(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_MODEL_API_KEY", "private-provider-secret")
    store = Store(tmp_path)
    template = store.create("template")
    store.update(
        template["id"],
        "failed",
        error="Шаблон не изучен",
        failure_diagnostics={
            "error_type": "ValueError",
            "model_calls": [
                {
                    "status": "failed",
                    "stage": "template_analyst",
                    "error_type": "HTTPStatusError",
                    "http_status": 401,
                    "provider_error_code": "private-provider-secret",
                    "request_payload": "must not copy model input",
                }
            ],
        },
    )
    preparation = store.create("preparation")
    runtime = JobRuntime(Settings(data_dir=tmp_path), store)
    asyncio.run(runtime._run_prepare(preparation["id"], template["id"]))

    job = store.get(preparation["id"])
    assert job["state"] == "failed"
    assert job["error"] == "Анализ шаблона не завершён"
    assert "failure_kind" not in job
    service = PresentationService(runtime.settings, store, runtime, version_operation=lambda: "v")
    report = service.diagnostics(preparation["id"])
    event = next(item for item in report["events"] if item["event"] == "template.dependency_failed")
    assert event["data"] == {
        "template_job_id": template["id"],
        "template_state": "failed",
        "stage": "template_analyst",
        "error_type": "HTTPStatusError",
        "http_status": 401,
        "provider_error_code": "[REDACTED]",
    }
    assert "request_payload" not in str(report)
    assert "private-provider-secret" not in str(report)
