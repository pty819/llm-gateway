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
