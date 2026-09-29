"""Owns background work and subprocess lifetime for one application instance."""

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any

from studio.config import ROOT, Settings
from studio.diagnostics import capture_stream, exception, redact, scope
from studio.jobs.store import Store


_OWNER_FILE = "worker-owner.json"
_PS_START = re.compile(r"^\s*(\S+\s+\S+\s+\S+\s+\S+\s+\S+)\s+(.+)$")


def _worker_identity(pid: int, jid: str, data_dir: Path) -> dict | None:
    """Identify only the exact worker command and process group we launched."""
    if os.name != "posix":
        return None
    try:
        output = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "lstart=", "-o", "command=", "-ww"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        match = _PS_START.match(output)
        if match is None or os.getpgid(pid) != pid:
            return None
        command = match.group(2).strip()
        expected = f" -m studio.jobs.worker {jid} {Path(data_dir).resolve()}"
        if not command.endswith(expected):
            return None
        return {
            "pid": pid,
            "started": match.group(1),
            "command": command,
            "job_id": jid,
            "data_dir": str(Path(data_dir).resolve()),
        }
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
        return None


def _save_worker_identity(store: Store, jid: str, pid: int) -> None:
    identity = _worker_identity(pid, jid, store.root)
    if identity is None:
        return
    path = store.directory(jid) / _OWNER_FILE
    temporary = path.with_name(f"{_OWNER_FILE}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(identity, stream)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def record_worker_identity(store: Store, jid: str) -> None:
    """Record the worker before it begins the job, including after a parent crash."""
    _save_worker_identity(store, jid, os.getpid())


def kill_worker(process):
    if getattr(process, "returncode", None) is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    except PermissionError:
        logging.getLogger(__name__).warning(
            "Group cleanup denied; stopping owned worker PID %s", process.pid
        )
        try:
            process.kill()
        except ProcessLookupError:
            pass


def reap_worker_group(process):
    """Stop descendants that outlived a worker that started its own session."""
    if os.name != "posix":
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        logging.getLogger(__name__).warning("Could not reap worker group %s", process.pid)


class JobRuntime:
    def __init__(
        self,
        settings: Settings,
        store: Store,
    ):
        self.settings = settings
        self.store = store
        self.start_generation: Callable[..., dict[str, Any]] | None = None
        self.on_prepared: Callable[[str], None] | None = None
        self.processes = {}
        self.tasks = set()
        self.job_tasks = {}
        self.template_tasks = {}
        self.auto_tasks = {}
        self.prep_slots = asyncio.Semaphore(2)

    def spawn(self, coroutine, jid=None):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        if jid is not None:
            self.job_tasks[jid] = task

        def done(finished):
            self.tasks.discard(finished)
            if jid is not None and self.job_tasks.get(jid) is finished:
                self.job_tasks.pop(jid, None)
            if not finished.cancelled() and (error := finished.exception()) is not None:
                logging.getLogger(__name__).error("Background job %s failed", jid, exc_info=error)

        task.add_done_callback(done)
        return task

    async def startup(self):
        await self._reap_previous_workers()
        self.store.recover()
        for job in self.store.scheduled():
            self.schedule_auto_generation(job["id"])

    async def _reap_previous_workers(self):
        for path in (self.store.root / "jobs").glob(f"*/{_OWNER_FILE}"):
            jid = path.parent.name
            clear_record = False
            try:
                self.store.directory(jid)
                saved = json.loads(path.read_text())
                pid = saved["pid"]
                if not isinstance(pid, int) or pid <= 0:
                    clear_record = True
                    continue
                current = _worker_identity(pid, jid, self.store.root)
                if current != saved:
                    clear_record = True
                    continue
                # Recheck immediately before signalling to narrow PID reuse races.
                if _worker_identity(pid, jid, self.store.root) != saved:
                    clear_record = True
                    continue
                os.killpg(pid, signal.SIGKILL)
                for _ in range(40):
                    if _worker_identity(pid, jid, self.store.root) != saved:
                        clear_record = True
                        break
                    await asyncio.sleep(0.05)
            except (OSError, ValueError, KeyError, TypeError):
                logging.getLogger(__name__).warning("Could not verify old worker for job %s", jid)
            finally:
                if clear_record:
                    path.unlink(missing_ok=True)

    def schedule_auto_generation(self, pid: str) -> None:
        active = self.auto_tasks.get(pid)
        if active is not None and not active.done():
            return
        task = self.spawn(self.auto_generate(pid))
        self.auto_tasks[pid] = task
        task.add_done_callback(
            lambda done, pid=pid: (
                self.auto_tasks.pop(pid, None) if self.auto_tasks.get(pid) is done else None
            )
        )

    async def shutdown(self):
        for process in self.processes.values():
            if process.returncode is None:
                kill_worker(process)
        pending = list(self.tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    def analyze_template(self, jid: str) -> None:
        task = self.spawn(self._run_template(jid), jid)
        self.template_tasks[jid] = task
        task.add_done_callback(lambda _, jid=jid: self.template_tasks.pop(jid, None))

    async def _run_template(self, jid):
        try:
            async with self.prep_slots:
                await self._supervise(self.store.get(jid))
        except asyncio.CancelledError:
            self.store.cancel_active(jid)
            raise
        except Exception as exc:
            with scope(self.store, jid):
                exception("template.supervisor_failed", exc)
            self.store.update(
                jid, "failed", error="Не удалось изучить шаблон. Подробности в журнале."
            )

    def prepare(self, jid: str, template_job_id: str | None = None):
        self.spawn(self._run_prepare(jid, template_job_id), jid)

    async def _run_prepare(self, jid, template_job_id):
        try:
            if template_job_id and (task := self.template_tasks.get(template_job_id)):
                await asyncio.shield(task)
            template = self.store.get(template_job_id) if template_job_id else None
            if template and template["state"] != "ready":
                diagnostics = template.get("failure_diagnostics") or {}
                failed_call = next(
                    (
                        call
                        for call in reversed(diagnostics.get("model_calls", []))
                        if call.get("status") == "failed"
                    ),
                    {},
                )
                details = {"template_job_id": template_job_id, "template_state": template["state"]}
                for key in ("stage", "error_type", "http_status", "provider_error_code"):
                    if isinstance(failed_call.get(key), (str, int)):
                        details[key] = failed_call[key]
                if "error_type" not in details and isinstance(diagnostics.get("error_type"), str):
                    details["error_type"] = diagnostics["error_type"]
                self.store.log(jid, "template.dependency_failed", level="error", **details)
                self.store.update(jid, "failed", error="Анализ шаблона не завершён")
                return
            async with self.prep_slots:
                await self._supervise(self.store.get(jid))
                job = self.store.get(jid)
                if job["state"] == "ready" and self.on_prepared is not None:
                    self.on_prepared(jid)
        except asyncio.CancelledError:
            self.store.cancel_active(jid)
            raise
        except Exception as exc:
            with scope(self.store, jid):
                exception("preparation.supervisor_failed", exc)
            self.store.update(
                jid,
                "failed",
                error="Не удалось завершить анализ. Подробности в журнале.",
                phase="Анализ остановлен",
            )

    async def auto_generate(self, pid: str) -> None:
        try:
            job = self.store.get(pid)
            await asyncio.sleep(max(0, job["auto_generate_at"] - time.time()))
            if self.store.get(pid).get("auto_generation") != "scheduled":
                return
        except KeyError:
            return  # The user deleted this scheduled package from history.
        try:
            if self.start_generation is None:
                raise RuntimeError("Generation callback is not configured")
            self.start_generation(
                pid,
                automatic=True,
                accept_adjusted_slide_count=bool(job.get("constraints", {}).get("confirm_plan")),
            )
        except KeyError:
            return
        except Exception as exc:
            detail = str(exc)
            self.store.update(pid, auto_generation="blocked", auto_error=redact(detail))
            self.store.log(pid, "generation.autostart_failed", level="error", message=detail)

    def supervise(self, job: dict) -> None:
        self.spawn(self._supervise(job), job["id"])

    async def cancel(self, jid: str) -> dict:
        if not self.store.cancel_active(jid, reason="Отменено пользователем"):
            raise ValueError("Задание уже завершено")
        process = self.processes.get(jid)
        if process is not None:
            kill_worker(process)
        task = self.job_tasks.get(jid)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        elif process is not None:
            await process.wait()
        return self.store.get(jid)

    async def _supervise(self, job):
        process = None
        reader = None
        launch = None
        try:
            launch = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "studio.jobs.worker",
                    job["id"],
                    str(self.store.root),
                    cwd=ROOT,
                    env=self.settings.worker_environment(),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
            )
            process = await asyncio.shield(launch)
            self.processes[job["id"]] = process
            _save_worker_identity(self.store, job["id"], process.pid)
            reader = asyncio.create_task(capture_stream(self.store, job["id"], process.stdout))
            await asyncio.wait_for(
                process.wait(),
                max(0.01, job["deadline_at"] - time.time())
                if job.get("deadline_at") is not None
                else None,
            )
            if self.store.get(job["id"])["state"] in ("accepted", "running"):
                self.store.update(
                    job["id"], "failed", error="Рабочий процесс завершился без результата"
                )
        except asyncio.TimeoutError:
            self.store.update(
                job["id"],
                "timed_out",
                phase="Время истекло",
                error="Истёк явно заданный административный лимит задания",
            )
            if process and process.returncode is None:
                kill_worker(process)
                await process.wait()
        except asyncio.CancelledError:
            if process is None and launch is not None:
                # Shield the launch until we know whether a child must be reaped.
                try:
                    process = await launch
                except Exception:
                    process = None
            if process and process.returncode is None:
                kill_worker(process)
            self.store.cancel_active(job["id"])
            raise
        except Exception as exc:
            with scope(self.store, job["id"]):
                exception("worker.supervisor_failed", exc)
            self.store.update(job["id"], "failed", error="Не удалось запустить рабочий процесс")
        finally:
            if process:
                if process.returncode is None:
                    kill_worker(process)
                await process.wait()
                reap_worker_group(process)
            if reader:
                await asyncio.gather(reader, return_exceptions=True)
            self.processes.pop(job["id"], None)
            (self.store.directory(job["id"]) / _OWNER_FILE).unlink(missing_ok=True)
