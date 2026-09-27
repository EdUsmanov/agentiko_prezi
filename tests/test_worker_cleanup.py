from types import SimpleNamespace
from studio import app


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
