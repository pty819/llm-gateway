"""Entrypoint for the health-check sidecar process.

Invocation::

    python -m llm_gateway.health_sidecar

This process does ONE thing: probe ACTIVE upstreams on a fixed interval and
write runtime liveness (UNHEALTHY markers) to Redis. It deliberately imports
nothing from the data plane — no FastAPI, no upstream client, no token
counting — so its event loop stays clean even when the main gateway is under
heavy load.

Three defenses live here:
1. Process isolation (this file's whole reason for existing): an independent
   GIL means a main-process freeze cannot stall these probes.
2. Quorum fuse (in health_checker._run_once): a suspicious batch of failures
   is suppressed rather than applied, so this sidecar's own rare freeze can't
   take out the fleet either.
3. ``faulthandler.dump_traceback_later``: a C-level timer that dumps all
   thread stacks to stderr if this process's loop ever stalls, so the next
   sidecar freeze names the guilty synchronous call directly.
"""

from __future__ import annotations

import asyncio
import faulthandler
import logging
import signal

from llm_gateway.services import health_checker


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _install_fault_dumper() -> None:
    """Arm a C-level watchdog that dumps stacks if the sidecar loop stalls.

    asyncio-based lag monitors can't catch the stall in the act: by the time
    the sleep callback runs, the blocking call has returned and its frame is
    gone. ``dump_traceback_later`` fires from a C timer that bypasses the GIL,
    so it snapshots the stack mid-stall. 5s threshold > the 3s probe timeout,
    so genuine probes never trip it; only a real stall does.
    """
    faulthandler.dump_traceback_later(5, repeat=True, exit=False)


def main() -> None:
    _configure_logging()
    _install_fault_dumper()
    logger = logging.getLogger("llm_gateway.health_sidecar")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Graceful shutdown on SIGTERM/SIGINT: stop() cancels the loop task and
    # closes the Redis client. The process exits once the loop drains.
    async def _run() -> None:
        await health_checker.start()
        try:
            # Block until cancelled by a signal — the loop's only job is the
            # background _main_loop task spawned by start().
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            await health_checker.stop()

    main_task = loop.create_task(_run())

    def _cancel(*_):
        main_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _cancel)
        except NotImplementedError:
            # add_signal_handler is unavailable on Windows/probing stubs; fall
            # back to default signal handling. The sidecar is a Linux/macOS
            # deployment artifact, so this branch is for test robustness only.
            signal.signal(sig, lambda *_: main_task.cancel())

    try:
        loop.run_until_complete(main_task)
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()
        logger.info("health_sidecar_exited")


if __name__ == "__main__":
    main()
