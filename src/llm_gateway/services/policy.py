from dataclasses import dataclass
from ipaddress import ip_address, ip_network

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway.db.models import (
    IPPolicyMode,
    ModelAlias,
    ModelEntitlement,
    ResourceState,
    UpstreamTarget,
)
from llm_gateway.services.security import AuthContext


class PolicyDenied(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class RouteContext:
    model_alias: ModelAlias
    upstream: UpstreamTarget


def client_ip_allowed(model_alias: ModelAlias, client_ip: str) -> bool:
    if model_alias.ip_policy_mode == IPPolicyMode.ALL_PASS:
        return True
    try:
        parsed_ip = ip_address(client_ip)
    except ValueError:
        return False
    for cidr in model_alias.ip_allowlist_cidrs:
        try:
            if parsed_ip in ip_network(cidr, strict=False):
                return True
        except ValueError:
            return False
    return False


async def resolve_route_context(
    session: AsyncSession,
    *,
    auth: AuthContext,
    requested_model: str,
    client_ip: str,
) -> RouteContext:
    alias_result = await session.execute(select(ModelAlias).where(ModelAlias.alias == requested_model))
    model_alias = alias_result.scalar_one_or_none()
    if not model_alias or model_alias.state != ResourceState.ACTIVE:
        raise PolicyDenied("model_alias_not_found_or_inactive")

    entitlement_result = await session.execute(
        select(ModelEntitlement).where(
            ModelEntitlement.model_alias_id == model_alias.id,
            ModelEntitlement.state == ResourceState.ACTIVE,
            or_(
                ModelEntitlement.gateway_key_id == auth.key.id,
                ModelEntitlement.subject_id == auth.subject.id,
                ModelEntitlement.project_id == auth.project.id,
            ),
        )
    )
    if not entitlement_result.scalars().first():
        raise PolicyDenied("model_not_entitled")

    if not client_ip_allowed(model_alias, client_ip):
        raise PolicyDenied("model_ip_denied")

    upstream_result = await session.execute(
        select(UpstreamTarget).where(
            UpstreamTarget.model_alias_id == model_alias.id,
            UpstreamTarget.state == ResourceState.ACTIVE,
        )
    )
    upstream = upstream_result.scalar_one_or_none()
    if not upstream:
        raise PolicyDenied("upstream_not_configured")

    return RouteContext(model_alias=model_alias, upstream=upstream)

