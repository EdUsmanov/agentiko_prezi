"""Shared provider admission and bounded HTTP retries across both worktrees.

Only one request per provider credential is in flight across app workers and
library builders. Waiting for admission/rate-limit cooldown is not a generation
deadline. Actual network work keeps its bounded timeout; attempts are bounded.
"""

import asyncio
from contextlib import asynccontextmanager
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
import os
import re
from pathlib import Path
import stat
import tempfile
import time
import uuid
from urllib.parse import urlsplit
import httpx
from studio.diagnostics import event


def retry_delay(response, attempt=0):
    value = response.headers.get("Retry-After", "")
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            seconds = 30 * 2**attempt
    if not 0 <= seconds < float("inf"):
        seconds = 30
    return min(300, max(0.5, seconds))


class ProviderGate:
    def __init__(self, settings, root=None, *, background=False):
        host = urlsplit(settings.base_url).netloc.casefold()
        identity = hashlib.sha256((host + "\0" + settings.api_key).encode()).hexdigest()
        self.root = Path(root or tempfile.gettempdir()) / f"vk-forma-provider-{os.getuid()}"
        self.path = self.root / (identity + ".lock")
        self.fd = None
        self.background = background
        self.ticket = None
        self.ticket_fd = None

    def _enqueue(self):
        # Publish only after taking the lease. An unlocked ticket belongs to a
        # crashed/cancelled process and must never block future requests.
        self.ticket_fd, temporary = tempfile.mkstemp(prefix=".queue-", dir=self.root)
        try:
            fcntl.flock(self.ticket_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.ticket = self.root / (
                self.path.stem
                + ".queue."
                + str(int(self.background))
                + "."
                + f"{time.time_ns():020d}."
                + uuid.uuid4().hex
            )
            os.replace(temporary, self.ticket)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            os.close(self.ticket_fd)
            self.ticket_fd = None
            raise

    def _first(self):
        active = []
        for path in self.root.glob(self.path.stem + ".queue.*"):
            if path == self.ticket:
                active.append(path.name)
                continue
            try:
                fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
            except FileNotFoundError:
                continue
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    active.append(path.name)
                else:
                    path.unlink(missing_ok=True)
            finally:
                os.close(fd)
        return bool(active) and self.ticket.name == min(active)

    def _dequeue(self):
        if self.ticket is not None:
            self.ticket.unlink(missing_ok=True)
            self.ticket = None
        if self.ticket_fd is not None:
            os.close(self.ticket_fd)
            self.ticket_fd = None

    async def __aenter__(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("Unsafe provider lock directory")
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            self._enqueue()
            while True:
                if not self._first():
                    await asyncio.sleep(0.05)
                    continue
                try:
                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.1)
            self._dequeue()
            os.lseek(self.fd, 0, os.SEEK_SET)
            try:
                until = float(os.read(self.fd, 100) or b"0")
            except ValueError:
                until = 0
            delay = max(0, min(300, until - time.time()))
            if delay:
                await asyncio.sleep(delay)
            return self
        except BaseException:
            self._dequeue()
            os.close(self.fd)
            self.fd = None
            raise

    def cooldown(self, seconds):
        os.lseek(self.fd, 0, os.SEEK_SET)
        os.write(self.fd, str(time.time() + seconds).encode())
        os.ftruncate(self.fd, os.lseek(self.fd, 0, os.SEEK_CUR))

    async def __aexit__(self, *args):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
            # FIFO tickets, not polling timing, determine who gets the next
            # slot. Foreground jobs take precedence over background libraries.


@asynccontextmanager
async def admission(settings, transport):
    # Injected transports are deterministic test doubles, never exposed by HTTP.
    if transport is not None:
        yield None
    else:
        from studio.diagnostics import background_work

        async with ProviderGate(settings, background=background_work()) as gate:
            yield gate


@asynccontextmanager
async def client_scope(client, transport, timeout):
    if client is not None:
        yield client
    else:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, trust_env=False, transport=transport
        ) as created:
            yield created


async def completion(settings, body, *, timeout, record, limiter, transport=None, client=None):
    queued = time.monotonic()
    event("model.waiting", stage=record.get("stage"))
    async with limiter, admission(settings, transport) as gate:
        record["queue_seconds"] = round(time.monotonic() - queued, 3)
        event(
            "model.admitted",
            stage=record.get("stage"),
            queue_seconds=record["queue_seconds"],
            network_timeout_seconds=timeout,
        )
        remaining = timeout
        rate_attempt = 0
        transient_attempt = 0
        headers = {"Authorization": "Bearer " + settings.api_key} if settings.api_key else {}
        async with client_scope(client, transport, timeout) as client:
            for attempt in range(4):
                if remaining <= 0:
                    raise TimeoutError("Provider network budget exhausted")
                record["attempts"] = attempt + 1
                started = time.monotonic()
                network_elapsed = None
                try:
                    event(
                        "model.request",
                        stage=record.get("stage"),
                        attempt=attempt + 1,
                        network_timeout_seconds=round(remaining, 3),
                    )
                    async with asyncio.timeout(remaining):
                        response = await client.post(
                            settings.base_url.rstrip("/") + "/chat/completions",
                            headers=headers,
                            json=body,
                            timeout=remaining,
                        )
                        response.raise_for_status()
                        return response.json()
                except (httpx.TransportError, httpx.HTTPStatusError) as error:
                    network_elapsed = time.monotonic() - started
                    remaining -= network_elapsed
                    status = (
                        error.response.status_code
                        if isinstance(error, httpx.HTTPStatusError)
                        else None
                    )
                    if status:
                        record["http_status"] = status
                        try:
                            detail = error.response.json().get("error", {})
                            code = (
                                detail.get("code") or detail.get("type")
                                if isinstance(detail, dict)
                                else None
                            )
                            if isinstance(code, str) and re.fullmatch(
                                r"[a-zA-Z0-9_.-]{1,64}", code
                            ):
                                record["provider_error_code"] = code
                        except (ValueError, AttributeError):
                            pass
                        if record.get("provider_error_code") in (
                            "insufficient_quota",
                            "quota_exceeded",
                            "billing_hard_limit_reached",
                        ):
                            raise
                    if status == 429:
                        delay = retry_delay(error.response, rate_attempt)
                        if gate:
                            gate.cooldown(delay)
                        record["retry_after_seconds"] = delay
                        if attempt == 3:
                            raise
                        event(
                            "model.rate_limited",
                            "warning",
                            stage=record.get("stage"),
                            http_status=429,
                            wait_seconds=delay,
                            attempt=attempt + 1,
                        )
                        record["backoff_seconds"] = record.get("backoff_seconds", 0) + delay
                        # Explicit provider cooldown is not charged as network work.
                        await asyncio.sleep(delay)
                        rate_attempt += 1
                    elif (status is None or status in (500, 502, 503, 504)) and attempt < 3:
                        delay = 2**transient_attempt
                        # Leave time for another request; never extend the caller's budget.
                        if remaining <= delay:
                            raise
                        transient_attempt += 1
                        event(
                            "model.retrying",
                            "warning",
                            stage=record.get("stage"),
                            error_type=type(error).__name__,
                            http_status=status,
                            wait_seconds=delay,
                            attempt=attempt + 1,
                        )
                        record["backoff_seconds"] = record.get("backoff_seconds", 0) + delay
                        await asyncio.sleep(delay)
                        remaining -= delay
                    else:
                        raise
                finally:
                    record["provider_seconds"] = round(
                        record.get("provider_seconds", 0)
                        + (
                            network_elapsed
                            if network_elapsed is not None
                            else time.monotonic() - started
                        ),
                        3,
                    )
            raise RuntimeError("Provider retry budget exhausted")
