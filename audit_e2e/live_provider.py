"""A request-bounded loopback proxy for explicitly authorized live evaluations."""

from base64 import b64decode
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit

import httpx


MAX_REQUEST_BYTES = 30_000_000
_HOP_BY_HOP = {"connection", "content-encoding", "content-length", "host", "transfer-encoding"}
_CURRENT_STAGE = ContextVar("evaluation_provider_stage", default="unknown")


@contextmanager
def evaluation_stage(stage):
    """Tag one physical transport request without changing the provider payload."""
    token = _CURRENT_STAGE.set(str(stage))
    try:
        yield
    finally:
        _CURRENT_STAGE.reset(token)


def _safe(value, secret=""):
    if isinstance(value, dict):
        return {
            str(key): (
                "[redacted]"
                if str(key).casefold() in {"authorization", "api_key", "api-key"}
                else _safe(item, secret)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe(item, secret) for item in value]
    if isinstance(value, str):
        if secret:
            value = value.replace(secret, "[redacted]")
        return re.sub(r"(?i)(bearer\s+)[^\s,;\"']+", r"\1[redacted]", value)
    return value


def _compact_images(value):
    """Store inline image identity and byte size, never bulky base64 in evidence."""
    if isinstance(value, dict):
        output = {key: _compact_images(item) for key, item in value.items()}
        image = output.get("image_url")
        if isinstance(image, dict) and isinstance(image.get("url"), str):
            url = image["url"]
            if url.startswith("data:image/") and ";base64," in url:
                header, encoded = url.split(",", 1)
                try:
                    raw = b64decode(encoded, validate=True)
                    image["url"] = {
                        "data_sha256": sha256(raw).hexdigest(),
                        "bytes": len(raw),
                        "mime": header[5:].split(";", 1)[0],
                    }
                except (ValueError, TypeError):
                    image["url"] = "[invalid inline image]"
        return output
    if isinstance(value, list):
        return [_compact_images(item) for item in value]
    return value


def _usage(response_body):
    usage = response_body.get("usage") if isinstance(response_body, dict) else None
    if not isinstance(usage, dict):
        return None
    prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    compact = {}
    for target, candidates in (
        ("input_tokens", ("prompt_tokens", "input_tokens")),
        ("output_tokens", ("completion_tokens", "output_tokens")),
        ("total_tokens", ("total_tokens",)),
    ):
        value = next(
            (usage[name] for name in candidates if isinstance(usage.get(name), (int, float))),
            None,
        )
        if value is not None:
            compact[target] = value
    for target, details, candidates in (
        ("cached_input_tokens", prompt_details, ("cached_tokens", "cache_read_tokens")),
        ("reasoning_tokens", completion_details, ("reasoning_tokens",)),
    ):
        value = next(
            (
                details[name]
                for name in candidates
                if isinstance(details, dict) and isinstance(details.get(name), (int, float))
            ),
            None,
        )
        if value is not None:
            compact[target] = value
    return compact or None


class LiveProvider:
    """Forward to one configured provider endpoint under a shared physical-call cap."""

    def __init__(self, settings, *, max_requests: int, timeout: float, request_dir=None):
        if max_requests <= 0 or timeout <= 0:
            raise ValueError("Live evaluations require positive request and time limits")
        parsed = urlsplit(settings.base_url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Invalid upstream provider endpoint")
        self.upstream_url = settings.base_url.rstrip("/") + "/chat/completions"
        self.max_requests = int(max_requests)
        self.timeout = max(0.1, min(float(timeout), 120.0))
        self._secret = settings.api_key
        self.request_dir = Path(request_dir).resolve() if request_dir is not None else None
        if self.request_dir is not None:
            self.request_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.request_dir.chmod(0o700)
        self._lock = threading.Lock()
        self._requests = 0
        self._blocked = 0
        self._failures = 0
        self._statuses = Counter()
        self._records = {}
        self._closed = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                if self.path != "/proxy":
                    self._send(404, {"error": {"code": "not_found"}})
                    return
                try:
                    size = int(self.headers.get("content-length", "0"))
                except ValueError:
                    size = 0
                if not 0 < size <= MAX_REQUEST_BYTES:
                    self._send(413, {"error": {"code": "request_too_large"}})
                    return
                payload = self.rfile.read(size)
                if len(payload) != size:
                    self._send(400, {"error": {"code": "invalid_request"}})
                    return
                with owner._lock:
                    if owner._requests >= owner.max_requests:
                        owner._blocked += 1
                        sequence = None
                    else:
                        owner._requests += 1
                        sequence = owner._requests
                if sequence is None:
                    self._send(403, {"error": {"code": "test_request_budget_exhausted"}})
                    return

                headers = {
                    name: value
                    for name, value in self.headers.items()
                    if name.casefold() not in _HOP_BY_HOP
                    and not name.casefold().startswith("x-test-")
                }
                stage = self.headers.get("X-Test-Stage", "unknown")
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", stage):
                    stage = "unknown"
                try:
                    request_body = json.loads(payload)
                except (ValueError, TypeError):
                    request_body = {"sha256": sha256(payload).hexdigest(), "bytes": len(payload)}

                request_started = time.monotonic()
                owner._write_attempt(
                    sequence, stage, request_body, {"state": "in_flight"}, "in_flight", 0
                )
                try:
                    with httpx.Client(
                        trust_env=False,
                        follow_redirects=False,
                        timeout=owner.timeout,
                    ) as client:
                        upstream = client.post(owner.upstream_url, content=payload, headers=headers)
                    status = upstream.status_code
                    raw = upstream.content
                    response_headers = {
                        name: value
                        for name, value in upstream.headers.items()
                        if name.casefold() not in _HOP_BY_HOP
                    }
                    try:
                        response_body = upstream.json()
                    except ValueError:
                        response_body = {"text": upstream.text[:20_000], "bytes": len(raw)}
                except httpx.HTTPError:
                    status = 502
                    raw = b'{"error":{"code":"upstream_transport_error"}}'
                    response_headers = {"content-type": "application/json"}
                    response_body = {"error": {"code": "upstream_transport_error"}}

                elapsed = time.monotonic() - request_started
                with owner._lock:
                    owner._statuses[status] += 1
                    if status >= 400:
                        owner._failures += 1
                owner._write_attempt(sequence, stage, request_body, response_body, status, elapsed)
                self._send(status, raw, response_headers)

            def _send(self, status, payload, headers=None):
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                for name, value in (headers or {}).items():
                    if name.casefold() not in _HOP_BY_HOP:
                        self.send_header(name, value)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="evaluation-live-provider", daemon=True
        )
        self._thread.start()
        self.proxy_url = f"http://127.0.0.1:{self._server.server_port}/proxy"

    def _write_attempt(self, sequence, stage, request_body, response_body, status, elapsed):
        usage = _usage(response_body)
        request_file = None
        if self.request_dir is not None:
            target = self.request_dir / f"attempt-{sequence:05d}.json"
            record = {
                "sequence": sequence,
                "stage": stage,
                "status": status,
                "duration_ms": round(elapsed * 1000, 1),
                "usage": usage,
                "request": _safe(_compact_images(request_body), self._secret),
                "response": _safe(response_body, self._secret),
            }
            temporary = target.with_suffix(".json.tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temporary, target)
            target.chmod(0o600)
            request_file = str(target.relative_to(self.request_dir.parent))
        row = {
            "sequence": sequence,
            "stage": stage,
            "status": status,
            "duration_ms": round(elapsed * 1000, 1),
            "usage": usage,
            "request_file": request_file,
        }
        with self._lock:
            self._records[sequence] = row

    def summary(self):
        with self._lock:
            rows = [self._records[key] for key in sorted(self._records)]
            usages = [row["usage"] for row in rows if row["usage"]]
            keys = (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "cached_input_tokens",
                "reasoning_tokens",
            )
            return {
                "model_requests": self._requests,
                "budget_blocked": self._blocked,
                "provider_failures": self._failures,
                "provider_http_statuses": {
                    str(status): count for status, count in sorted(self._statuses.items())
                },
                "provider_requests": rows,
                "generator_usage": {
                    **{
                        key: sum(row[key] for row in usages if key in row)
                        if any(key in row for row in usages)
                        else None
                        for key in keys
                    },
                    "reported_requests": len(usages),
                },
            }

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


class LoopbackProxyTransport(httpx.AsyncBaseTransport):
    """Keep original URL/provider branching while routing bytes via the local cap."""

    def __init__(self, proxy_url: str):
        self.proxy_url = proxy_url

    async def handle_async_request(self, request):
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.casefold() not in _HOP_BY_HOP
        }
        stage = _CURRENT_STAGE.get()
        if stage != "unknown":
            headers["X-Test-Stage"] = stage
        timeout_values = request.extensions.get("timeout", {})
        timeout = httpx.Timeout(
            connect=timeout_values.get("connect") or 10,
            read=timeout_values.get("read") or 60,
            write=timeout_values.get("write") or 60,
            pool=timeout_values.get("pool") or 10,
        )
        async with httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=timeout
        ) as client:
            response = await client.post(
                self.proxy_url, headers=headers, content=await request.aread()
            )
            content = await response.aread()
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=content,
            request=request,
        )


def install_worker_transport_from_env():
    """Install an explicit test-only transport in inherited app/worker processes."""
    proxy_url = os.environ.get("STUDIO_TEST_LIVE_PROXY_URL")
    if not proxy_url:
        return
    parsed = urlsplit(proxy_url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path != "/proxy":
        raise RuntimeError("Live evaluation proxy must be a loopback HTTP endpoint")
    from studio.providers.gateway import ModelGateway

    original = ModelGateway.provider_client
    original_request = ModelGateway._json_request

    async def tagged_request(self, prompt_name, *args, **kwargs):
        token = _CURRENT_STAGE.set(prompt_name)
        try:
            return await original_request(self, prompt_name, *args, **kwargs)
        finally:
            _CURRENT_STAGE.reset(token)

    def provider_client(self):
        loop = __import__("asyncio").get_running_loop()
        if loop not in self._clients:
            self._clients[loop] = httpx.AsyncClient(
                follow_redirects=False,
                trust_env=False,
                transport=LoopbackProxyTransport(proxy_url),
                limits=httpx.Limits(
                    max_connections=self.settings.model_concurrency,
                    max_keepalive_connections=self.settings.model_concurrency,
                ),
            )
        return self._clients[loop]

    def guarded_provider_client(self):
        if self.transport is not None:
            return original(self)
        return provider_client(self)

    ModelGateway.provider_client = guarded_provider_client
    ModelGateway._json_request = tagged_request
