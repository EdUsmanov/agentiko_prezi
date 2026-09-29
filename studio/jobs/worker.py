import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from studio.config import Settings
from studio.jobs.store import Store
from studio.models import BriefDraft, Constraints, ContentModel
from studio.pipeline import generate, preanalyze_template, prepare
from pydantic import ValidationError
from studio.checks.quality_gate import QualityGateRejected


def run_job(store, jid, settings):
    job = store.get(jid)
    if job["kind"] == "template":
        preanalyze_template(store, jid, settings)
    elif job["kind"] == "preparation":
        request = json.loads((store.directory(jid) / "request.json").read_text())
        prepare(
            store,
            jid,
            request["text"],
            request["audience"],
            request["instructions"],
            request["slides"],
            settings,
            ContentModel.model_validate(request["content_model"])
            if request.get("content_model") is not None
            else None,
            Constraints.model_validate(request["base_constraints"])
            if request.get("base_constraints") is not None
            else None,
            input_mode=request.get("input_mode", "content"),
            allowed_fact_ids=request.get("allowed_fact_ids"),
            draft=BriefDraft.model_validate(request["draft"])
            if request.get("draft") is not None
            else None,
        )
    elif job["kind"] == "generation":
        asyncio.run(generate(store, jid, settings))
    else:
        raise ValueError("Неизвестный тип задания")


def main():
    settings = replace(Settings.from_worker_env(), data_dir=Path(sys.argv[2]))
    store = Store(settings.data_dir)
    from studio.jobs.runtime import record_worker_identity

    record_worker_identity(store, sys.argv[1])
    from studio.diagnostics import configure, scope, exception

    configure(settings.api_key)
    try:
        with scope(store, sys.argv[1]):
            run_job(store, sys.argv[1], settings)
    except QualityGateRejected as exc:
        with scope(store, sys.argv[1]):
            exception("quality.rejected", exc)
        store.update(
            sys.argv[1],
            "failed",
            error=str(exc),
            phase="Требуется исправление замечаний",
            failure_kind="quality_gate",
            review_available=True,
        )
    except TimeoutError as exc:
        with scope(store, sys.argv[1]):
            exception("worker.timeout", exc)
        store.update(
            sys.argv[1],
            "failed",
            error="Истёк тайм-аут отдельной операции. Подробности в журнале.",
            phase="Операция остановлена",
        )
    except Exception as exc:
        with scope(store, sys.argv[1]):
            exception("worker.failed", exc)
        # Avoid leaking payloads, credentials or stack traces into UI.
        message = (
            str(exc)
            if isinstance(exc, ValueError) and not isinstance(exc, ValidationError)
            else "Ошибка обработки (" + type(exc).__name__ + ")"
        )
        store.update(sys.argv[1], "failed", error=message, phase="Обработка остановлена")


if __name__ == "__main__":
    main()
