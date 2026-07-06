"""Smoke tests for the health-check sidecar entrypoint.

The sidecar's main() creates its own event loop and blocks on signal handling,
which makes it unsuitable to invoke directly inside a pytest-asyncio session.
Instead we verify the importable pieces: logging config, the faulthandler
watchdog, and that start/stop are wired correctly (the sidecar delegates to
health_checker.start/stop, which are tested in test_health_checker.py).
"""

from __future__ import annotations

from llm_gateway.health_sidecar import __main__ as sidecar_main


def test_install_fault_dumper_does_not_raise():
    """The faulthandler watchdog must arm without error on import.

    We don't assert it fires (that needs a real 5s stall); we just assert the
    sidecar's startup path doesn't crash on a platform without faulthandler
    support or with an already-armed timer.
    """
    # Should not raise; faulthandler.dump_traceback_later is idempotent.
    sidecar_main._install_fault_dumper()


def test_configure_logging_does_not_raise():
    sidecar_main._configure_logging()


def test_sidecar_imports_health_checker():
    """The sidecar module must import health_checker (its only job)."""
    assert sidecar_main.health_checker is not None
    assert hasattr(sidecar_main.health_checker, "start")
    assert hasattr(sidecar_main.health_checker, "stop")


def test_main_callable():
    """main() is defined and callable (we don't invoke it — see module docstring)."""
    assert callable(sidecar_main.main)
