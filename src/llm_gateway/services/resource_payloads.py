from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from llm_gateway.db.models import GatewayKey, UpstreamTarget, utcnow


def redact_upstream(upstream: UpstreamTarget) -> dict[str, Any]:
    data = upstream.model_dump()
    data["api_key_value"] = None
    data["has_api_key"] = bool(upstream.api_key_value or upstream.api_key_ref)
    return data


def redact_gateway_key(key: GatewayKey) -> dict[str, Any]:
    data = key.model_dump()
    data["key_hash"] = None
    return data


def apply_model_patch(target, payload: BaseModel) -> None:
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(target, key, value)
    target.updated_at = utcnow()


def paginated(items: Sequence, total: int, limit: int | None, offset: int) -> dict:
    return {
        "items": items,
        "total": total,
        "limit": limit if limit is not None else total,
        "offset": offset,
    }


def skill_summary(skill, owner_name: str | None = None) -> dict[str, Any]:
    return {
        "id": str(skill.id),
        "owner_subject_id": str(skill.owner_subject_id),
        "owner_name": owner_name,
        "slug": skill.slug,
        "name": skill.name,
        "summary": skill.summary,
        "state": skill.state.value if hasattr(skill.state, "value") else skill.state,
        "latest_version": skill.latest_version,
        "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
        "download_count": skill.download_count,
        "like_count": skill.like_count,
    }


def skill_detail(
    skill,
    versions,
    grants,
    owner_name: str | None = None,
    *,
    readme: str | None = None,
    liked_by_me: bool = False,
) -> dict[str, Any]:
    return {
        **skill_summary(skill, owner_name=owner_name),
        "description": skill.description,
        "notes": skill.notes,
        "readme": readme,
        "liked_by_me": liked_by_me,
        "versions": [
            {
                "version": v.version,
                "content_sha256": v.content_sha256,
                "size_bytes": v.size_bytes,
                "upload_subject_id": str(v.upload_subject_id),
                "state": v.state.value if hasattr(v.state, "value") else v.state,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in versions
        ],
        "grants": [
            {
                "id": str(g.id),
                "skill_id": str(g.skill_id),
                "team_id": str(g.team_id),
                "state": g.state.value if hasattr(g.state, "value") else g.state,
            }
            for g in grants
        ],
    }


_MCP_SENSITIVE_VERSION_KEYS = ("env", "headers")


def redact_mcp_version(version, *, reveal: bool = False) -> dict[str, Any]:
    """Serialize an McpVersion to a dict. env/headers values are replaced with
    '***' unless reveal=True (owner/admin only). tools are never redacted."""
    data = {
        "version": version.version,
        "transport": version.transport.value
        if hasattr(version.transport, "value")
        else version.transport,
        "command": version.command,
        "args": list(version.args or []),
        "env": dict(version.env or {}),
        "url": version.url,
        "headers": dict(version.headers or {}),
        "tools": list(version.tools or []),
        "upload_subject_id": str(version.upload_subject_id),
        "state": version.state.value if hasattr(version.state, "value") else version.state,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }
    if not reveal:
        for key in _MCP_SENSITIVE_VERSION_KEYS:
            data[key] = {k: "***" for k in (data[key] or {})}
    return data


def mcp_summary(mcp, owner_name: str | None = None) -> dict[str, Any]:
    return {
        "id": str(mcp.id),
        "owner_subject_id": str(mcp.owner_subject_id),
        "owner_name": owner_name,
        "slug": mcp.slug,
        "name": mcp.name,
        "summary": mcp.summary,
        "state": mcp.state.value if hasattr(mcp.state, "value") else mcp.state,
        "latest_version": mcp.latest_version,
        "updated_at": mcp.updated_at.isoformat() if mcp.updated_at else None,
        "download_count": mcp.download_count,
        "like_count": mcp.like_count,
    }


def mcp_detail(
    mcp,
    versions,
    latest_version,
    grants,
    owner_name: str | None = None,
    *,
    reveal: bool = False,
    liked_by_me: bool = False,
    readme: str | None = None,
) -> dict[str, Any]:
    """versions are serialized with redaction per `reveal`. latest_version is the
    resolved latest McpVersion row (or None) also serialized with redaction."""
    detail = {
        **mcp_summary(mcp, owner_name=owner_name),
        "description": mcp.description,
        "notes": mcp.notes,
        "readme": readme,
        "liked_by_me": liked_by_me,
        "versions": [redact_mcp_version(v, reveal=reveal) for v in versions],
        "latest": redact_mcp_version(latest_version, reveal=reveal) if latest_version else None,
        "grants": [
            {
                "id": str(g.id),
                "mcp_id": str(g.mcp_id),
                "team_id": str(g.team_id),
                "state": g.state.value if hasattr(g.state, "value") else g.state,
            }
            for g in grants
        ],
    }
    return detail
