"""Benchmark: prove the disconnect watchdog does not block the event loop.

Spawns N concurrent ``iter_with_heartbeat`` streams, each with a slow upstream
(one event then a long sleep) and a disconnect watchdog running at the given
``disconnect_interval``. While they run, a latency probe coroutine measures the
realized tick latency of ``asyncio.sleep(0.01)`` — if the watchdog (or any
other task) were blocking the loop, the actual elapsed time would spike well
above the expected 10 ms.

Usage::

    uv run python scripts/bench_watchdog.py
    uv run python scripts/bench_watchdog.py --interval 0.1 --duration 3
    uv run python scripts/bench_watchdog.py --concurrency 1000 --interval 0.05

Verdict: PASS when max tick latency < 50 ms (well under human-perceptible).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import statistics
import time
from collections.abc import AsyncIterator
from typing import Any

from llm_gateway.services.streaming import iter_with_heartbeat

# Expected sleep duration for the latency probe (10 ms tick).
PROBE_INTERVAL = 0.01
# A PASS requires max tick latency below this threshold.
PASS_THRESHOLD_MS = 50.0


async def _slow_upstream() -> AsyncIterator[tuple[str, dict[str, Any] | None]]:
    """Yield one event, then sleep essentially forever (slow model)."""
    yield "data: first\n\n", None
    # Simulate a model that thinks for a very long time. The heartbeat +
    # watchdog keep the stream alive during this silence.
    await asyncio.sleep(3600)


def _noop_disconnect_check() -> Any:
    """A disconnect check that does a trivial amount of awaitable work.

    The point of the benchmark is the *scheduling* overhead of N watchdogs
    firing concurrently, not the internals of Starlette's
    ``request.is_disconnected()`` (which is itself non-blocking). A single
    ``asyncio.sleep(0)`` yields back to the scheduler, matching the cost
    shape of a real check without pulling in anyio/Starlette.
    """

    async def _check() -> bool:
        await asyncio.sleep(0)
        return False

    return _check()


async def _run_one_stream(interval: float, disconnect_interval: float) -> None:
    """Drive a single heartbeat stream until cancelled."""
    agen = iter_with_heartbeat(
        _slow_upstream(),
        interval_seconds=interval,
        disconnect_check=_noop_disconnect_check,
        disconnect_interval=disconnect_interval,
    )
    try:
        async for _ in agen:
            # We only care about the watchdog/heartbeat running; consume
            # whatever frames arrive and keep the stream open.
            pass
    except asyncio.CancelledError:
        # Clean shutdown at the end of the benchmark — swallow it.
        with contextlib.suppress(BaseException):
            await agen.aclose()
        raise


async def _latency_probe(duration: float) -> tuple[list[float], float]:
    """Measure realized tick latency of ``asyncio.sleep(PROBE_INTERVAL)``.

    Returns (samples_in_ms, wall_clock_elapsed).
    """
    samples: list[float] = []
    deadline = time.perf_counter() + duration
    loop = asyncio.get_running_loop()
    while time.perf_counter() < deadline:
        t0 = loop.time()
        await asyncio.sleep(PROBE_INTERVAL)
        t1 = loop.time()
        samples.append((t1 - t0) * 1000.0)
    wall = duration
    return samples, wall


async def _run_benchmark(
    concurrency: int, interval: float, disconnect_interval: float, duration: float
) -> dict[str, Any]:
    """Run the benchmark and return a stats dict."""
    streams = [
        asyncio.create_task(_run_one_stream(interval, disconnect_interval))
        for _ in range(concurrency)
    ]
    probe = asyncio.create_task(_latency_probe(duration))

    # Give the streams a moment to spin up their watchdog/heartbeat tasks
    # before we start measuring (the probe measures for `duration` wall time).
    await asyncio.sleep(0)

    samples, wall = await probe

    # Tear down all streams.
    for t in streams:
        t.cancel()
    await asyncio.gather(*streams, return_exceptions=True)

    samples.sort()
    n = len(samples)
    max_ms = samples[-1] if samples else 0.0
    mean_ms = statistics.fmean(samples) if samples else 0.0
    # p99 via nearest-rank method.
    p99_idx = max(0, min(n - 1, int(0.99 * n) - 1))
    p99_ms = samples[p99_idx] if samples else 0.0

    verdict = "PASS" if max_ms < PASS_THRESHOLD_MS else "FAIL"

    return {
        "concurrency": concurrency,
        "heartbeat_interval_s": interval,
        "disconnect_interval_s": disconnect_interval,
        "duration_s": round(wall, 3),
        "samples": n,
        "max_ms": round(max_ms, 3),
        "p99_ms": round(p99_ms, 3),
        "mean_ms": round(mean_ms, 3),
        "expected_ms": PROBE_INTERVAL * 1000,
        "threshold_ms": PASS_THRESHOLD_MS,
        "verdict": verdict,
    }


def _print_report(stats: dict[str, Any]) -> None:
    print("=" * 64)
    print("watchdog event-loop latency benchmark")
    print("=" * 64)
    print(f"  concurrent streams      : {stats['concurrency']}")
    print(f"  heartbeat interval      : {stats['heartbeat_interval_s']}s")
    print(f"  disconnect interval     : {stats['disconnect_interval_s']}s")
    print(f"  benchmark duration      : {stats['duration_s']}s")
    print(f"  probe samples           : {stats['samples']}")
    print(f"  expected tick           : {stats['expected_ms']}ms")
    print("-" * 64)
    print(f"  max  tick latency       : {stats['max_ms']}ms")
    print(f"  p99  tick latency       : {stats['p99_ms']}ms")
    print(f"  mean tick latency       : {stats['mean_ms']}ms")
    print("-" * 64)
    print(f"  pass threshold (max<)   : {stats['threshold_ms']}ms")
    print(f"  VERDICT                 : {stats['verdict']}")
    print("=" * 64)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-n",
        "--concurrency",
        type=int,
        default=500,
        help="number of concurrent streams (default: 500)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="disconnect_check interval in seconds (default: 5.0, production)",
    )
    parser.add_argument(
        "--heartbeat",
        type=float,
        default=1.0,
        help="heartbeat keepalive interval in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--duration", type=float, default=3.0, help="benchmark duration in seconds (default: 3.0)"
    )
    args = parser.parse_args()

    stats = await _run_benchmark(
        concurrency=args.concurrency,
        interval=args.heartbeat,
        disconnect_interval=args.interval,
        duration=args.duration,
    )
    _print_report(stats)

    if stats["verdict"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
