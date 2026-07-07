"""Small shared helpers for the auth route family.

Used by both ``api/auth_session.py`` and ``api/managed.py``. Kept here (rather
than in one of those modules) to avoid an import cycle: ``_public_subject`` is
referenced by the managed subject listing, while ``_profile_payload`` in the
session module consumes managed payloads.

Anything in here must remain dependency-free on the route modules — only
services / models / stdlib imports.
"""

from __future__ import annotations

from typing import Any

from llm_gateway.db.models import Subject
from llm_gateway.services.security import normalize_username


def public_subject(subject: Subject) -> dict[str, Any]:
    """Public (non-sensitive) projection of a Subject for API responses."""
    return {
        "id": subject.id,
        "name": subject.name,
        "type": subject.type,
        "state": subject.state,
        "notes": subject.notes,
        "login_username": subject.login_username,
        "is_admin": subject.is_admin,
        "requires_real_name": requires_real_name(subject),
        "created_at": subject.created_at,
        "updated_at": subject.updated_at,
    }


def requires_real_name(subject: Subject) -> bool:
    if subject.is_admin:
        return False
    name = subject.name.strip()
    username = normalize_username(subject.login_username or "")
    return not name or (bool(username) and normalize_username(name) == username)
