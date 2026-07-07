"""Shared test helpers.

These helpers used to live at module level in ``test_backend_integration.py``.
Several unrelated test files import them (``_auth_headers`` / ``_employee_username``),
so splitting the god-test-file required relocating them to a stable home here.
"""

from __future__ import annotations

from uuid import uuid4


def _auth_headers(raw_key: str, request_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {raw_key}"}
    if request_id:
        headers["x-request-id"] = request_id
    return headers


def _employee_username() -> str:
    return f"l{uuid4().int % 100_000_000:08d}"
