"""Real isolated HTTP server and optional cassette provider, including child workers."""

from contextlib import contextmanager
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
from threading import Thread
import time
import httpx
from studio.config import Settings
from .replay import Replay

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def application(directory: Path, *, cassette: Path | None = None):
    directory.mkdir(parents=True, exist_ok=True)
    replay = Replay(cassette) if cassette else None
    provider = None
    if replay:

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != "/v1/chat/completions":
                    self.send_error(404)
                    return
                size = int(self.headers.get("content-length", "0"))
                if not 0 < size < 30_000_000:
                    self.send_error(413)
                    return
                status, payload, headers = replay.respond(json.loads(self.rfile.read(size)))
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        provider = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        Thread(target=provider.serve_forever, daemon=True).start()
    settings = Settings(data_dir=directory / "data")
    if provider:
        from dataclasses import replace

        settings = replace(
            settings,
            mode="api",
            execution_kind="replay",
            base_url=f"http://127.0.0.1:{provider.server_port}/v1",
            model_id="fixture-27b",
            parameters_b=27,
            open_weights=True,
            license="Apache-2.0",
            structured_output=True,
            engine="deeppresenter",
            visual_review=True,
        )
    values = asdict(settings)
    values["data_dir"] = str(settings.data_dir)
    config = directory / "settings.json"
    config.write_text(json.dumps(values))
    config.chmod(0o600)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("STUDIO_", "LLM_", "OPENROUTER_"))
    }
    env["PYTHONPATH"] = str(ROOT)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    with (directory / "server.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "test_support.serve",
                "--settings",
                str(config),
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            with httpx.Client(trust_env=False, timeout=1) as client:
                for _ in range(200):
                    if process.poll() is not None:
                        raise AssertionError((directory / "server.log").read_text())
                    try:
                        if client.get(url + "/api/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise TimeoutError("Isolated browser server did not start")
            yield url, replay, settings
        finally:
            if process.poll() is None:
                # Collect API diagnostics before stopping only our server, including failures.
                try:
                    with httpx.Client(trust_env=False, timeout=2) as client:
                        jobs = client.get(url + "/api/jobs").raise_for_status().json()
                        for job in jobs:
                            response = client.get(url + "/api/jobs/" + job["id"] + "/diagnostics")
                            response.raise_for_status()
                            (directory / (job["id"] + "-diagnostics.json")).write_text(
                                json.dumps(response.json(), ensure_ascii=False, indent=2)
                            )
                except (httpx.HTTPError, ValueError):
                    pass  # Startup failures still have server.log; don't hide the test error.
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            if provider:
                provider.shutdown()
                provider.server_close()
            if replay:
                (directory / "replay-report.json").write_text(
                    json.dumps(
                        {"calls": replay.calls, "mismatches": replay.mismatches},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
