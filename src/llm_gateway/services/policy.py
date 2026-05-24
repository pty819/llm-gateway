from dataclasses import dataclass
from ipaddress import ip_address, ip_network

from sqlalchemy import distinct, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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

    if not await subject_can_use_model(session, auth=auth, model_alias_id=model_alias.id):
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


async def subject_can_use_model(session: AsyncSession, *, auth: AuthContext, model_alias_id) -> bool:
    entitlement_result = await session.execute(
        select(ModelEntitlement.id).where(
            ModelEntitlement.model_alias_id == model_alias_id,
            ModelEntitlement.state == ResourceState.ACTIVE,
            or_(
                ModelEntitlement.gateway_key_id == auth.key.id,
                ModelEntitlement.subject_id == auth.subject.id,
                ModelEntitlement.project_id == auth.project.id,
            ),
        )
    )
    if entitlement_result.scalars().first():
        return True

    team_result = await session.execute(
        select(ModelTeamGrant.id)
        .join(Team, Team.id == ModelTeamGrant.team_id)
        .join(TeamMembership, TeamMembership.team_id == Team.id)
        .where(
            ModelTeamGrant.model_alias_id == model_alias_id,
            ModelTeamGrant.state == ResourceState.ACTIVE,
            Team.state == ResourceState.ACTIVE,
            TeamMembership.state == ResourceState.ACTIVE,
            TeamMembership.subject_id == auth.subject.id,
        )
    )
    return team_result.scalars().first() is not None


async def list_accessible_model_aliases(session: AsyncSession, *, auth: AuthContext) -> list[str]:
    legacy_stmt = (
        select(distinct(ModelAlias.alias))
        .join(ModelEntitlement, ModelEntitlement.model_alias_id == ModelAlias.id)
        .where(
            ModelAlias.state == ResourceState.ACTIVE,
            ModelEntitlement.state == ResourceState.ACTIVE,
            or_(
                ModelEntitlement.gateway_key_id == auth.key.id,
                ModelEntitlement.subject_id == auth.subject.id,
                ModelEntitlement.project_id == auth.project.id,
            ),
        )
    )
    team_stmt = (
        select(distinct(ModelAlias.alias))
        .join(ModelTeamGrant, ModelTeamGrant.model_alias_id == ModelAlias.id)
        .join(Team, Team.id == ModelTeamGrant.team_id)
        .join(TeamMembership, TeamMembership.team_id == Team.id)
        .where(
            ModelAlias.state == ResourceState.ACTIVE,
            ModelTeamGrant.state == ResourceState.ACTIVE,
            Team.state == ResourceState.ACTIVE,
            TeamMembership.state == ResourceState.ACTIVE,
            TeamMembership.subject_id == auth.subject.id,
        )
    )
    aliases = set((await session.execute(legacy_stmt)).scalars().all())
    aliases.update((await session.execute(team_stmt)).scalars().all())
    return sorted(aliases)


async def list_accessible_model_aliases_for_subject(session: AsyncSession, *, subject_id) -> list[str]:
    direct_stmt = (
        select(distinct(ModelAlias.alias))
        .join(ModelEntitlement, ModelEntitlement.model_alias_id == ModelAlias.id)
        .where(
            ModelAlias.state == ResourceState.ACTIVE,
            ModelEntitlement.state == ResourceState.ACTIVE,
            ModelEntitlement.subject_id == subject_id,
        )
    )
    team_stmt = (
        select(distinct(ModelAlias.alias))
        .join(ModelTeamGrant, ModelTeamGrant.model_alias_id == ModelAlias.id)
        .join(Team, Team.id == ModelTeamGrant.team_id)
        .join(TeamMembership, TeamMembership.team_id == Team.id)
        .where(
            ModelAlias.state == ResourceState.ACTIVE,
            ModelTeamGrant.state == ResourceState.ACTIVE,
            Team.state == ResourceState.ACTIVE,
            TeamMembership.state == ResourceState.ACTIVE,
            TeamMembership.subject_id == subject_id,
        )
    )
    aliases = set((await session.execute(direct_stmt)).scalars().all())
    aliases.update((await session.execute(team_stmt)).scalars().all())
    return sorted(aliases)


async def list_subject_team_names(session: AsyncSession, *, subject_id) -> list[str]:
    stmt = (
        select(Team.name)
        .join(TeamMembership, TeamMembership.team_id == Team.id)
        .where(
            Team.state == ResourceState.ACTIVE,
            TeamMembership.state == ResourceState.ACTIVE,
            TeamMembership.subject_id == subject_id,
        )
        .order_by(Team.name)
    )
    return list((await session.execute(stmt)).scalars().all())
