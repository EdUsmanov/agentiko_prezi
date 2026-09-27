import asyncio
import subprocess
import sys
from types import SimpleNamespace
import httpx
import pytest
from studio.provider_transport import ProviderGate, completion, retry_delay


def test_gate_coordinates_independent_instances_and_releases_on_cancel(tmp_path):
    settings = SimpleNamespace(
        base_url="https://example.test/v1", api_key="credential-not-a-real-key"
    )

    async def check():
        first = ProviderGate(settings, tmp_path)
        async with first:
            blocked = asyncio.create_task(ProviderGate(settings, tmp_path).__aenter__())
            await asyncio.sleep(0.15)
            assert not blocked.done()
            blocked.cancel()
            with pytest.raises(asyncio.CancelledError):
                await blocked
        async with ProviderGate(settings, tmp_path):
            pass
        assert "credential-not-a-real-key" not in first.path.name

    asyncio.run(check())


def test_busy_builder_yields_admission_to_waiting_peer(tmp_path):
    settings = SimpleNamespace(base_url="https://example.test/v1", api_key="test-only")
    order = []

    async def peer():
        async with ProviderGate(settings, tmp_path):
            order.append("peer")

    async def check():
        async with ProviderGate(settings, tmp_path):
            waiting = asyncio.create_task(peer())
            await asyncio.sleep(0.02)
        async with ProviderGate(settings, tmp_path):
            order.append("builder")
        await waiting

    asyncio.run(check())
    assert order == ["peer", "builder"]


def test_provider_identity_is_host_and_key_not_model_or_branch(tmp_path):
    a = SimpleNamespace(base_url="https://example.test/v1", api_key="one")
    b = SimpleNamespace(base_url="https://example.test/v2", api_key="one")
    c = SimpleNamespace(base_url="https://example.test/v1", api_key="two")
    assert ProviderGate(a, tmp_path).path == ProviderGate(b, tmp_path).path
    assert ProviderGate(a, tmp_path).path != ProviderGate(c, tmp_path).path


def test_foreground_precedes_background_and_equal_priority_is_fifo(tmp_path):
    settings = SimpleNamespace(base_url="https://example.test/v1", api_key="test-only")
    order = []

    async def enter(name, background):
        async with ProviderGate(settings, tmp_path, background=background):
            order.append(name)

    async def check():
        async with ProviderGate(settings, tmp_path):
            tasks = []
            for name, background in [
                ("library-1", True),
                ("library-2", True),
                ("user-1", False),
                ("user-2", False),
            ]:
                tasks.append(asyncio.create_task(enter(name, background)))
                await asyncio.sleep(0.01)
        await asyncio.gather(*tasks)

    asyncio.run(check())
    assert order == ["user-1", "user-2", "library-1", "library-2"]


def test_dead_queue_ticket_does_not_block_future_jobs(tmp_path):
    settings = SimpleNamespace(base_url="https://example.test/v1", api_key="test-only")
    gate = ProviderGate(settings, tmp_path)
    gate.root.mkdir(parents=True, mode=0o700)
    stale = gate.root / (gate.path.stem + ".queue.0.00000000000000000001.dead")
    stale.write_text("")  # A crashed process no longer owns the file lease.

    async def check():
        async with asyncio.timeout(1):
            async with gate:
                pass

    asyncio.run(check())
    assert not stale.exists()
    assert not list(gate.root.glob("*.queue.*"))


def test_background_identity_is_scoped_not_inferred_from_prompt():
    from studio.diagnostics import scope, background_work

    assert not background_work()
    with scope(None, "library"):
        assert background_work()
    with scope(None, "user-job"):
        assert not background_work()


def test_gate_coordinates_separate_processes(tmp_path):
    settings = SimpleNamespace(base_url="https://example.test/v1", api_key="test-only")
    child = """
import asyncio, sys
from types import SimpleNamespace
from studio.provider_transport import ProviderGate
async def check():
    try:
        async with asyncio.timeout(.3):
            async with ProviderGate(SimpleNamespace(base_url="https://example.test/v1",api_key="test-only"),sys.argv[1]):
                return 0
    except TimeoutError:
        return 73
sys.exit(asyncio.run(check()))
"""

    def run_child():
        return subprocess.run(
            [sys.executable, "-c", child, str(tmp_path)], capture_output=True, timeout=10
        ).returncode

    async def check():
        async with ProviderGate(settings, tmp_path):
            assert await asyncio.to_thread(run_child) == 73
        assert await asyncio.to_thread(run_child) == 0

    asyncio.run(check())


def test_429_wait_is_not_a_generation_or_network_deadline():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    async def check():
        record = {}
        result = await completion(
            SimpleNamespace(base_url="https://example.test/v1", api_key=""),
            {},
            timeout=0.1,
            record=record,
            limiter=asyncio.Semaphore(3),
            transport=httpx.MockTransport(handler),
        )
        assert result == {"ok": True}
        assert record["attempts"] == 2 and record["backoff_seconds"] == 0.5

    asyncio.run(check())


def test_retry_header_is_bounded():
    assert retry_delay(httpx.Response(429, headers={"Retry-After": "9999999"})) == 300
    assert retry_delay(httpx.Response(429)) == 30
    assert retry_delay(httpx.Response(429, headers={"Retry-After": "nan"})) == 30


def test_429_retry_budget_is_finite(monkeypatch):
    from studio import provider_transport as transport

    original = asyncio.sleep

    async def fast(_):
        await original(0)

    monkeypatch.setattr(transport.asyncio, "sleep", fast)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429)

    async def check():
        with pytest.raises(httpx.HTTPStatusError):
            await completion(
                SimpleNamespace(base_url="https://example.test/v1", api_key=""),
                {},
                timeout=1,
                record={},
                limiter=asyncio.Semaphore(1),
                transport=httpx.MockTransport(handler),
            )

    asyncio.run(check())
    assert len(calls) == 4


@pytest.mark.parametrize("success_on", [3, 4, None])
def test_transient_connection_failures_use_bounded_backoff(monkeypatch, success_on):
    from studio import provider_transport as transport

    delays = []

    async def fast(delay):
        delays.append(delay)

    monkeypatch.setattr(transport.asyncio, "sleep", fast)
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == success_on:
            return httpx.Response(200, json={"ok": True})
        raise httpx.ConnectError("TLS connection interrupted", request=request)

    async def check():
        record = {}
        request = completion(
            SimpleNamespace(base_url="https://example.test/v1", api_key=""),
            {},
            timeout=30,
            record=record,
            limiter=asyncio.Semaphore(1),
            transport=httpx.MockTransport(handler),
        )
        if success_on:
            assert await request == {"ok": True}
        else:
            with pytest.raises(httpx.ConnectError):
                await request
        assert record["attempts"] == (success_on or 4)
        assert record["backoff_seconds"] == sum(delays)

    asyncio.run(check())
    assert delays == [1, 2, 4][: (success_on or 4) - 1]


def test_network_retry_does_not_exceed_remaining_budget(monkeypatch):
    from studio import provider_transport as transport

    async def no_sleep(_):
        pytest.fail("No budget for backoff")

    monkeypatch.setattr(transport.asyncio, "sleep", no_sleep)
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("unavailable", request=request)

    async def check():
        with pytest.raises(httpx.ConnectError):
            await completion(
                SimpleNamespace(base_url="https://example.test/v1", api_key=""),
                {},
                timeout=0.5,
                record={},
                limiter=asyncio.Semaphore(1),
                transport=httpx.MockTransport(handler),
            )

    asyncio.run(check())
    assert len(calls) == 1


def test_authentication_error_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401)

    async def check():
        with pytest.raises(httpx.HTTPStatusError):
            await completion(
                SimpleNamespace(base_url="https://example.test/v1", api_key=""),
                {},
                timeout=30,
                record={},
                limiter=asyncio.Semaphore(1),
                transport=httpx.MockTransport(handler),
            )

    asyncio.run(check())
    assert len(calls) == 1
