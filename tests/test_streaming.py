import asyncio

import pytest

from llm_gateway.services.streaming import HEARTBEAT_FRAME, iter_with_heartbeat


async def _silence(seconds: float):
    await asyncio.sleep(seconds)


async def test_forwards_events_and_injects_heartbeats_during_silence():
    async def upstream():
        yield ("data: a\n\n", {"x": 1})
        await _silence(0.15)  # silent longer than the 0.05s interval
        yield ("data: b\n\n", {"x": 2})

    out: list[tuple[str, object]] = []
    async for event, usage in iter_with_heartbeat(upstream(), interval_seconds=0.05):
        out.append((event, usage))

    events = [e for e, _ in out]
    assert events[0] == "data: a\n\n"
    assert events[-1] == "data: b\n\n"
    assert HEARTBEAT_FRAME in events
    # keepalive frames never carry usage (must not pollute accounting)
    for event, usage in out:
        if event == HEARTBEAT_FRAME:
            assert usage is None


async def test_propagates_upstream_exception():
    async def upstream():
        yield ("data: a\n\n", None)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        async for _ in iter_with_heartbeat(upstream(), interval_seconds=0.05):
            pass


async def test_no_heartbeat_when_upstream_never_goes_silent():
    async def upstream():
        yield ("data: a\n\n", None)
        yield ("data: b\n\n", None)

    out = []
    async for event, _ in iter_with_heartbeat(upstream(), interval_seconds=1.0):
        out.append(event)

    assert out == ["data: a\n\n", "data: b\n\n"]


async def test_watchdog_breaks_loop_on_disconnect():
    async def upstream():
        yield ("data: a\n\n", None)
        await asyncio.sleep(10.0)  # long silence simulating a stuck generator

    checks = 0

    async def disconnect_check() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2  # False first, True second

    start = asyncio.get_event_loop().time()
    with pytest.raises(asyncio.CancelledError):
        async for _ in iter_with_heartbeat(
            upstream(),
            interval_seconds=1.0,
            disconnect_check=disconnect_check,
            disconnect_interval=0.02,
        ):
            pass
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed < 0.2, f"watchdog took too long: {elapsed:.3f}s"
    assert checks >= 2


async def test_watchdog_disabled_when_callback_none():
    async def upstream():
        yield ("data: a\n\n", None)
        yield ("data: b\n\n", None)

    out = []
    async for event, _ in iter_with_heartbeat(upstream(), interval_seconds=1.0):
        out.append(event)

    assert out == ["data: a\n\n", "data: b\n\n"]


async def test_watchdog_disabled_when_interval_zero():
    calls = 0

    async def disconnect_check() -> bool:
        nonlocal calls
        calls += 1
        return False

    async def upstream():
        yield ("data: a\n\n", None)
        yield ("data: b\n\n", None)

    out = []
    async for event, _ in iter_with_heartbeat(
        upstream(),
        interval_seconds=1.0,
        disconnect_check=disconnect_check,
        disconnect_interval=0,
    ):
        out.append(event)

    assert calls == 0
    assert out == ["data: a\n\n", "data: b\n\n"]


async def test_watchdog_does_not_interfere_normal_stream():
    async def disconnect_check() -> bool:
        return False

    async def upstream():
        yield ("data: a\n\n", None)
        await _silence(0.06)  # silent longer than heartbeat + watchdog intervals
        yield ("data: b\n\n", None)

    out = []
    async for event, _ in iter_with_heartbeat(
        upstream(),
        interval_seconds=0.02,
        disconnect_check=disconnect_check,
        disconnect_interval=0.02,
    ):
        out.append(event)

    assert out[0] == "data: a\n\n"
    assert out[-1] == "data: b\n\n"
    assert HEARTBEAT_FRAME in out
    # exactly the two real events plus at least one heartbeat, no CancelledError leak
    assert out.count("data: a\n\n") == 1
    assert out.count("data: b\n\n") == 1


async def test_watchdog_survives_failing_disconnect_check():
    """A disconnect_check that raises must not kill the watchdog (which would
    silently disable disconnect detection for the rest of the stream)."""

    async def upstream():
        yield ("data: a\n\n", None)
        await asyncio.sleep(10.0)  # long silence

    calls = 0

    async def disconnect_check() -> bool:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("transient request state error")
        return True  # disconnect detected once checks stabilize

    start = asyncio.get_event_loop().time()
    with pytest.raises(asyncio.CancelledError):
        async for _ in iter_with_heartbeat(
            upstream(),
            interval_seconds=1.0,
            disconnect_check=disconnect_check,
            disconnect_interval=0.02,
        ):
            pass
    elapsed = asyncio.get_event_loop().time() - start
    # watchdog kept polling past the failures and eventually fired
    assert calls >= 3
    assert elapsed < 0.5, f"watchdog died on failing check: {elapsed:.3f}s"
