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
