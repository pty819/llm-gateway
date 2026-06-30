from dataclasses import dataclass
from ipaddress import ip_address, ip_network

from redis.asyncio import Redis
from sqlalchemy import distinct, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.db.models import (
    IPPolicyMode,
    ModelAlias,
    ModelEntitlement,
    ModelTeamGrant,
    ResourceState,
    Team,
    TeamMembership,
    UpstreamTarget,
)
from llm_gateway.services.security import AuthContext
from llm_gateway.services.upstream_routing import select_upstream_for_key


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
    redis: Redis | None = None,
    auth: AuthContext,
    requested_model: str,
    client_ip: str,
) -> RouteContext:
    alias_result = await session.execute(
        select(ModelAlias).where(col(ModelAlias.alias) == requested_model)
    )
    model_alias = alias_result.scalar_one_or_none()
    if not model_alias or model_alias.state != ResourceState.ACTIVE:
        raise PolicyDenied("model_alias_not_found_or_inactive")

    if not await subject_can_use_model(
        session, auth=auth, model_alias_id=model_alias.id
    ):
        raise PolicyDenied("model_not_entitled")

    if not client_ip_allowed(model_alias, client_ip):
        raise PolicyDenied("model_ip_denied")

    upstream_result = await session.execute(
        select(UpstreamTarget)
        .where(
            col(UpstreamTarget.model_alias_id) == model_alias.id,
            col(UpstreamTarget.state) == ResourceState.ACTIVE,
        )
        .order_by(col(UpstreamTarget.created_at).asc())
    )
    upstreams = list(upstream_result.scalars().all())
    if not upstreams:
        raise PolicyDenied("upstream_not_configured")

    upstream, _loads = await select_upstream_for_key(
        redis,
        key_id=auth.key.id,
        model_alias=model_alias,
        upstreams=upstreams,
    )
    ctx = RouteContext(model_alias=model_alias, upstream=upstream)
    return ctx


async def subject_can_use_model(
    session: AsyncSession, *, auth: AuthContext, model_alias_id
) -> bool:
    # Access is re-evaluated on every request with no cache, so revoking a team
    # membership or disabling an entitlement takes effect immediately rather
    # than after a TTL window. The query is a couple of indexed point lookups,
    # cheap next to the upstream model call; correctness of access revocation
    # outweighs the small saving a short-lived cache would provide.
    entitlement_result = await session.execute(
        select(col(ModelEntitlement.id)).where(
            col(ModelEntitlement.model_alias_id) == model_alias_id,
            col(ModelEntitlement.state) == ResourceState.ACTIVE,
            or_(
                col(ModelEntitlement.gateway_key_id) == auth.key.id,
                col(ModelEntitlement.subject_id) == auth.subject.id,
                col(ModelEntitlement.project_id) == auth.project.id,
            ),
        )
    )
    if entitlement_result.scalars().first():
        return True

    team_result = await session.execute(
        select(col(ModelTeamGrant.id))
        .join(Team, col(Team.id) == col(ModelTeamGrant.team_id))
        .join(TeamMembership, col(TeamMembership.team_id) == col(Team.id))
        .where(
            col(ModelTeamGrant.model_alias_id) == model_alias_id,
            col(ModelTeamGrant.state) == ResourceState.ACTIVE,
            col(Team.state) == ResourceState.ACTIVE,
            col(TeamMembership.state) == ResourceState.ACTIVE,
            col(TeamMembership.subject_id) == auth.subject.id,
        )
    )
    return team_result.scalars().first() is not None


async def list_accessible_model_aliases(
    session: AsyncSession, *, auth: AuthContext
) -> list[str]:
    legacy_stmt = (
        select(distinct(col(ModelAlias.alias)))
        .join(
            ModelEntitlement, col(ModelEntitlement.model_alias_id) == col(ModelAlias.id)
        )
        .where(
            col(ModelAlias.state) == ResourceState.ACTIVE,
            col(ModelEntitlement.state) == ResourceState.ACTIVE,
            or_(
                col(ModelEntitlement.gateway_key_id) == auth.key.id,
                col(ModelEntitlement.subject_id) == auth.subject.id,
                col(ModelEntitlement.project_id) == auth.project.id,
            ),
        )
    )
    team_stmt = (
        select(distinct(col(ModelAlias.alias)))
        .join(ModelTeamGrant, col(ModelTeamGrant.model_alias_id) == col(ModelAlias.id))
        .join(Team, col(Team.id) == col(ModelTeamGrant.team_id))
        .join(TeamMembership, col(TeamMembership.team_id) == col(Team.id))
        .where(
            col(ModelAlias.state) == ResourceState.ACTIVE,
            col(ModelTeamGrant.state) == ResourceState.ACTIVE,
            col(Team.state) == ResourceState.ACTIVE,
            col(TeamMembership.state) == ResourceState.ACTIVE,
            col(TeamMembership.subject_id) == auth.subject.id,
        )
    )
    aliases = set((await session.execute(legacy_stmt)).scalars().all())
    aliases.update((await session.execute(team_stmt)).scalars().all())
    return sorted(aliases)


async def list_accessible_model_aliases_for_subject(
    session: AsyncSession, *, subject_id
) -> list[str]:
    direct_stmt = (
        select(distinct(col(ModelAlias.alias)))
        .join(
            ModelEntitlement, col(ModelEntitlement.model_alias_id) == col(ModelAlias.id)
        )
        .where(
            col(ModelAlias.state) == ResourceState.ACTIVE,
            col(ModelEntitlement.state) == ResourceState.ACTIVE,
            col(ModelEntitlement.subject_id) == subject_id,
        )
    )
    team_stmt = (
        select(distinct(col(ModelAlias.alias)))
        .join(ModelTeamGrant, col(ModelTeamGrant.model_alias_id) == col(ModelAlias.id))
        .join(Team, col(Team.id) == col(ModelTeamGrant.team_id))
        .join(TeamMembership, col(TeamMembership.team_id) == col(Team.id))
        .where(
            col(ModelAlias.state) == ResourceState.ACTIVE,
            col(ModelTeamGrant.state) == ResourceState.ACTIVE,
            col(Team.state) == ResourceState.ACTIVE,
            col(TeamMembership.state) == ResourceState.ACTIVE,
            col(TeamMembership.subject_id) == subject_id,
        )
    )
    aliases = set((await session.execute(direct_stmt)).scalars().all())
    aliases.update((await session.execute(team_stmt)).scalars().all())
    return sorted(aliases)


async def list_subject_team_names(session: AsyncSession, *, subject_id) -> list[str]:
    stmt = (
        select(col(Team.name))
        .join(TeamMembership, col(TeamMembership.team_id) == col(Team.id))
        .where(
            col(Team.state) == ResourceState.ACTIVE,
            col(TeamMembership.state) == ResourceState.ACTIVE,
            col(TeamMembership.subject_id) == subject_id,
        )
        .order_by(col(Team.name))
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_subject_team_memberships(
    session: AsyncSession, *, subject_id
) -> list[dict]:
    """Return the active teams a subject belongs to as ``{id, name}`` dicts.

    Mirrors list_subject_team_names but selects Team.id alongside Team.name so
    callers (e.g. the self-service grants editor) can reference a team by id.
    Includes built-in teams like ``guest`` since the subject is a member.
    """
    stmt = (
        select(Team.id, Team.name)
        .join(TeamMembership, col(TeamMembership.team_id) == col(Team.id))
        .where(
            col(Team.state) == ResourceState.ACTIVE,
            col(TeamMembership.state) == ResourceState.ACTIVE,
            col(TeamMembership.subject_id) == subject_id,
        )
        .order_by(col(Team.name))
    )
    return [
        {"id": str(team_id), "name": team_name}
        for team_id, team_name in (await session.execute(stmt)).all()
    ]
