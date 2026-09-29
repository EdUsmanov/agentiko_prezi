from types import SimpleNamespace
import asyncio
import json
import os
import signal
import sys

from studio.jobs import runtime as app
from studio.config import Settings
from studio.jobs.store import Store


def test_completed_worker_is_not_signalled(monkeypatch):
    monkeypatch.setattr(
        app.os, "killpg", lambda *args: (_ for _ in ()).throw(AssertionError("already exited"))
    )
    app.kill_worker(SimpleNamespace(pid=123, returncode=0))


def test_group_permission_failure_falls_back_to_owned_child(monkeypatch):
    if app.os.name != "posix":
        return
    calls = []

    def denied(*args):
        raise PermissionError()

    monkeypatch.setattr(app.os, "killpg", denied)
    app.kill_worker(SimpleNamespace(pid=123, returncode=None, kill=lambda: calls.append("child")))
    assert calls == ["child"]


def test_cancel_waits_until_owned_worker_is_reaped(tmp_path, monkeypatch):
    store = Store(tmp_path)
    job = store.create("generation")
    runtime = app.JobRuntime(Settings(data_dir=tmp_path), store)
    launched = asyncio.Event()
    reaped = []

    class Child:
        pid = 987654
        returncode = None

        def __init__(self):
            self.stopped = asyncio.Event()
            self.stdout = asyncio.StreamReader()

        async def wait(self):
            await self.stopped.wait()
            return self.returncode

        def kill(self):
            self.returncode = -9
            self.stdout.feed_eof()
            self.stopped.set()

    children = []

    async def start(*args, **kwargs):
        child = Child()
        children.append(child)
        launched.set()
        return child

    monkeypatch.setattr(app.asyncio, "create_subprocess_exec", start)
    monkeypatch.setattr(app, "kill_worker", lambda process: process.kill())
    monkeypatch.setattr(app, "reap_worker_group", lambda process: reaped.append(process.pid))

    async def check():
        runtime.supervise(job)
        await launched.wait()
        await runtime.cancel(job["id"])
        child = children[0]
        assert child.returncode == -9
        assert reaped == [child.pid]
        assert store.get(job["id"])["state"] == "cancelled"
        store.update(job["id"], "completed")
        assert store.get(job["id"])["state"] == "cancelled"

    asyncio.run(check())


def test_cancel_during_worker_launch_still_reaps_child(tmp_path, monkeypatch):
    store = Store(tmp_path)
    job = store.create("generation")
    runtime = app.JobRuntime(Settings(data_dir=tmp_path), store)
    entering = asyncio.Event()
    release = asyncio.Event()
    killed = []

    class Child:
        pid = 987655
        returncode = None

        def __init__(self):
            self.stdout = asyncio.StreamReader()
            self.stopped = asyncio.Event()

        async def wait(self):
            await self.stopped.wait()

        def kill(self):
            killed.append(self.pid)
            self.returncode = -9
            self.stdout.feed_eof()
            self.stopped.set()

    async def start(*args, **kwargs):
        entering.set()
        await release.wait()
        return Child()

    monkeypatch.setattr(app.asyncio, "create_subprocess_exec", start)
    monkeypatch.setattr(app, "kill_worker", lambda process: process.kill())
    monkeypatch.setattr(app, "reap_worker_group", lambda process: None)

    async def check():
        runtime.supervise(job)
        await entering.wait()
        cancelling = asyncio.create_task(runtime.cancel(job["id"]))
        await asyncio.sleep(0)
        assert not cancelling.done()
        release.set()
        await cancelling
        assert killed == [987655]
        assert store.get(job["id"])["state"] == "cancelled"

    asyncio.run(check())


def test_restart_reaps_only_worker_with_matching_birth_and_command(tmp_path):
    if os.name != "posix":
        return
    store = Store(tmp_path / "data")
    job = store.create("generation")
    fake = tmp_path / "studio"
    fake.mkdir()
    (fake / "__init__.py").touch()
    (fake / "jobs").mkdir()
    (fake / "jobs/__init__.py").touch()
    (fake / "jobs/worker.py").write_text("import time\ntime.sleep(60)\n")

    async def check():
        owned = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "studio.jobs.worker",
            job["id"],
            str(store.root),
            cwd=tmp_path,
            start_new_session=True,
        )
        unrelated = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            start_new_session=True,
        )
        try:
            identity = None
            for _ in range(40):
                identity = app._worker_identity(owned.pid, job["id"], store.root)
                if identity is not None:
                    break
                await asyncio.sleep(0.05)
            assert identity is not None
            marker = store.directory(job["id"]) / app._OWNER_FILE
            marker.write_text(json.dumps(identity))
            await app.JobRuntime(Settings(data_dir=store.root), store).startup()
            await asyncio.wait_for(owned.wait(), 3)
            assert owned.returncode == -signal.SIGKILL
            assert unrelated.returncode is None
            assert store.get(job["id"])["state"] == "failed"
            assert not marker.exists()

            # A reused PID with different start identity cannot be signalled.
            marker.write_text(json.dumps({**identity, "pid": unrelated.pid}))
            await app.JobRuntime(Settings(data_dir=store.root), store).startup()
            assert unrelated.returncode is None
            assert not marker.exists()
        finally:
            for process in (owned, unrelated):
                if process.returncode is None:
                    process.kill()
                await process.wait()

    asyncio.run(check())
