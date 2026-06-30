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
    }


def skill_detail(skill, versions, grants, owner_name: str | None = None) -> dict[str, Any]:
    return {
        **skill_summary(skill, owner_name=owner_name),
        "description": skill.description,
        "notes": skill.notes,
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
