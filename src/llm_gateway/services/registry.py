from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.db.models import (
    ArtifactKind,
    MCP,
    MCPTransport,
    McpTeamGrant,
    McpVersion,
    ResourceState,
    Skill,
    SkillTeamGrant,
    SkillVersion,
    Subject,
    Team,
    TeamMembership,
    utcnow,
)
from llm_gateway.services.facts import record_audit_event


SLUG_PATTERN = r"^[a-z][a-z0-9-]*$"


async def resolve_owner_subject(
    session: AsyncSession, *, owner: str
) -> Subject | None:
    """Resolve a URL path `owner` to an active Subject.

    Prefer login_username (human-readable handle) then fall back to the
    Subject.name (used by service accounts that have no login_username).
    """
    stmt = select(Subject).where(
        col(Subject.state) == ResourceState.ACTIVE,
        or_(
            col(Subject.login_username) == owner,
            col(Subject.name) == owner,
        ),
    )
    return (await session.execute(stmt)).scalars().first()


async def subject_can_access_skill(
    session: AsyncSession, *, subject_id: UUID, skill: Skill
) -> bool:
    """A subject may see a skill iff it is the owner OR a team it belongs to has
    an active grant for the skill. Mirrors the team-grant branch of
    services/policy.py:subject_can_use_model."""
    if skill.owner_subject_id == subject_id:
        return True
    result = await session.execute(
        select(col(SkillTeamGrant.id))
        .join(Team, col(Team.id) == col(SkillTeamGrant.team_id))
        .join(TeamMembership, col(TeamMembership.team_id) == col(Team.id))
        .where(
            col(SkillTeamGrant.skill_id) == skill.id,
            col(SkillTeamGrant.state) == ResourceState.ACTIVE,
            col(Team.state) == ResourceState.ACTIVE,
            col(TeamMembership.state) == ResourceState.ACTIVE,
            col(TeamMembership.subject_id) == subject_id,
        )
    )
    return result.scalars().first() is not None


async def get_skill_by_owner_slug(
    session: AsyncSession, *, owner_id: UUID, slug: str, include_disabled: bool = False
) -> Skill | None:
    stmt = select(Skill).where(
        col(Skill.owner_subject_id) == owner_id,
        col(Skill.slug) == slug,
    )
    if not include_disabled:
        stmt = stmt.where(col(Skill.state) == ResourceState.ACTIVE)
    return (await session.execute(stmt)).scalars().first()


async def create_or_append_skill_version(
    session: AsyncSession,
    *,
    actor: Subject,
    slug: str,
    version: str,
    name: str,
    summary: str | None,
    description: str | None,
    notes: str | None,
    zip_bytes: bytes,
) -> Skill:
    """Create a skill (first version) or append a new version.

    Namespacing is (owner, slug): a different owner may use the same slug
    (alice/weather and bob/weather coexist), enforced by the composite
    UNIQUE(owner_subject_id, slug) constraint. This function only resolves
    the actor's own (actor.id, slug) row.
    If (actor, slug) does not exist -> create the skill + first version.
    If it exists (actor is the owner) -> append a new version, make it latest.
    Duplicate version string on the same skill -> 409 version_conflict.
    """
    existing = await get_skill_by_owner_slug(
        session, owner_id=actor.id, slug=slug, include_disabled=True
    )

    sha = hashlib.sha256(zip_bytes).hexdigest()

    if existing is None:
        skill = Skill(
            owner_subject_id=actor.id,
            slug=slug,
            name=name,
            summary=summary,
            description=description,
            notes=notes,
            latest_version=version,
        )
        session.add(skill)
        await session.flush()
        session.add(
            SkillVersion(
                skill_id=skill.id,
                version=version,
                content_blob=zip_bytes,
                content_sha256=sha,
                size_bytes=len(zip_bytes),
                upload_subject_id=actor.id,
            )
        )
        await session.flush()
        action = "skill.create"
    else:
        dup = await session.execute(
            select(col(SkillVersion.id)).where(
                col(SkillVersion.skill_id) == existing.id,
                col(SkillVersion.version) == version,
            )
        )
        if dup.scalars().first() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="version_conflict"
            )
        existing.name = name
        existing.summary = summary
        existing.description = description
        if notes is not None:
            existing.notes = notes
        existing.latest_version = version
        existing.updated_at = utcnow()
        session.add(
            SkillVersion(
                skill_id=existing.id,
                version=version,
                content_blob=zip_bytes,
                content_sha256=sha,
                size_bytes=len(zip_bytes),
                upload_subject_id=actor.id,
            )
        )
        await session.flush()
        skill = existing
        action = "skill.upload_version"

    await record_audit_event(
        session,
        action=action,
        resource_type="skill",
        resource_id=skill.id,
        outcome="success",
        actor_subject_id=actor.id,
        detail={"slug": slug, "version": version, "sha256": sha[:16]},
    )
    return skill


async def ensure_skill_team_grant(
    session: AsyncSession, *, skill_id: UUID, team_id: UUID
) -> SkillTeamGrant:
    """Idempotent grant upsert: reactivate if exists, else create.
    Mirrors services/security.py:ensure_model_team_grant."""
    result = await session.execute(
        select(SkillTeamGrant).where(
            col(SkillTeamGrant.skill_id) == skill_id,
            col(SkillTeamGrant.team_id) == team_id,
        )
    )
    grant = result.scalar_one_or_none()
    if grant:
        if grant.state != ResourceState.ACTIVE:
            grant.state = ResourceState.ACTIVE
            grant.updated_at = utcnow()
        return grant
    grant = SkillTeamGrant(skill_id=skill_id, team_id=team_id)
    session.add(grant)
    await session.flush()
    return grant


async def list_visible_skills(
    session: AsyncSession,
    *,
    subject_id: UUID,
    q: str | None = None,
    owner: str | None = None,
    limit: int = 30,
    offset: int = 0,
) -> tuple[list[Skill], int]:
    """Return skills visible to subject_id (owner of OR team-granted), with search."""
    base_filter = [
        col(Skill.state) == ResourceState.ACTIVE,
        or_(
            col(Skill.owner_subject_id) == subject_id,
            col(Skill.id).in_(
                select(distinct(col(SkillTeamGrant.skill_id)))
                .join(Team, col(Team.id) == col(SkillTeamGrant.team_id))
                .join(
                    TeamMembership,
                    col(TeamMembership.team_id) == col(Team.id),
                )
                .where(
                    col(SkillTeamGrant.state) == ResourceState.ACTIVE,
                    col(Team.state) == ResourceState.ACTIVE,
                    col(TeamMembership.state) == ResourceState.ACTIVE,
                    col(TeamMembership.subject_id) == subject_id,
                )
            ),
        ),
    ]
    if q:
        needle = f"%{q}%"
        base_filter.append(
            or_(
                col(Skill.name).ilike(needle),
                col(Skill.summary).ilike(needle),
                col(Skill.slug).ilike(needle),
            )
        )
    if owner:
        base_filter.append(
            col(Skill.owner_subject_id).in_(
                select(col(Subject.id)).where(
                    or_(
                        col(Subject.login_username) == owner,
                        col(Subject.name) == owner,
                    )
                )
            )
        )
    count_stmt = select(func.count(distinct(col(Skill.id)))).where(*base_filter)
    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    list_stmt = (
        select(Skill)
        .where(*base_filter)
        .order_by(col(Skill.updated_at).desc())
        .limit(limit)
        .offset(offset)
    )
    items = list((await session.execute(list_stmt)).scalars().all())
    return items, total


async def get_skill_version(
    session: AsyncSession, *, skill_id: UUID, version: str
) -> SkillVersion | None:
    stmt = select(SkillVersion).where(
        col(SkillVersion.skill_id) == skill_id,
        col(SkillVersion.version) == version,
        col(SkillVersion.state) == ResourceState.ACTIVE,
    )
    return (await session.execute(stmt)).scalars().first()


async def get_latest_active_version(
    session: AsyncSession, *, skill: Skill
) -> SkillVersion | None:
    """Resolve the latest_version pointer; if it points at a disabled row or is
    null, fall back to the most recent active version by created_at."""
    if skill.latest_version:
        pointed = await get_skill_version(
            session, skill_id=skill.id, version=skill.latest_version
        )
        if pointed:
            return pointed
    stmt = (
        select(SkillVersion)
        .where(
            col(SkillVersion.skill_id) == skill.id,
            col(SkillVersion.state) == ResourceState.ACTIVE,
        )
        .order_by(col(SkillVersion.created_at).desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


# ---- MCP artifact (connection config) ----


async def subject_can_access_mcp(
    session: AsyncSession, *, subject_id: UUID, mcp: MCP
) -> bool:
    """Same visibility rule as skills: owner OR active team grant."""
    if mcp.owner_subject_id == subject_id:
        return True
    result = await session.execute(
        select(col(McpTeamGrant.id))
        .join(Team, col(Team.id) == col(McpTeamGrant.team_id))
        .join(TeamMembership, col(TeamMembership.team_id) == col(Team.id))
        .where(
            col(McpTeamGrant.mcp_id) == mcp.id,
            col(McpTeamGrant.state) == ResourceState.ACTIVE,
            col(Team.state) == ResourceState.ACTIVE,
            col(TeamMembership.state) == ResourceState.ACTIVE,
            col(TeamMembership.subject_id) == subject_id,
        )
    )
    return result.scalars().first() is not None


async def get_mcp_by_owner_slug(
    session: AsyncSession, *, owner_id: UUID, slug: str, include_disabled: bool = False
) -> MCP | None:
    stmt = select(MCP).where(
        col(MCP.owner_subject_id) == owner_id,
        col(MCP.slug) == slug,
    )
    if not include_disabled:
        stmt = stmt.where(col(MCP.state) == ResourceState.ACTIVE)
    return (await session.execute(stmt)).scalars().first()


def _validate_mcp_config(config: dict) -> dict:
    """Validate + normalize an MCP connection config dict. Returns a clean dict
    with transport as MCPTransport and defaults filled."""
    transport = config.get("transport")
    try:
        transport_enum = MCPTransport(transport)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid_transport",
        )
    if transport_enum == MCPTransport.STDIO and not config.get("command"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="stdio_requires_command",
        )
    if transport_enum in (MCPTransport.HTTP, MCPTransport.SSE) and not config.get("url"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="remote_requires_url",
        )
    return {
        "transport": transport_enum,
        "command": config.get("command"),
        "args": list(config.get("args") or []),
        "env": dict(config.get("env") or {}),
        "url": config.get("url"),
        "headers": dict(config.get("headers") or {}),
        "tools": list(config.get("tools") or []),
    }


async def create_or_append_mcp_version(
    session: AsyncSession,
    *,
    actor: Subject,
    slug: str,
    version: str,
    name: str,
    summary: str | None,
    description: str | None,
    notes: str | None,
    config: dict,
) -> MCP:
    """Create an MCP config (first version) or append a new version.
    Namespacing is (owner, slug); a different owner may reuse the same slug.
    Duplicate version string on the same mcp -> 409 version_conflict."""
    cfg = _validate_mcp_config(config)
    existing = await get_mcp_by_owner_slug(
        session, owner_id=actor.id, slug=slug, include_disabled=True
    )

    if existing is None:
        mcp = MCP(
            owner_subject_id=actor.id,
            slug=slug,
            name=name,
            summary=summary,
            description=description,
            notes=notes,
            latest_version=version,
        )
        session.add(mcp)
        await session.flush()
        session.add(
            McpVersion(
                mcp_id=mcp.id,
                version=version,
                transport=cfg["transport"],
                command=cfg["command"],
                args=cfg["args"],
                env=cfg["env"],
                url=cfg["url"],
                headers=cfg["headers"],
                tools=cfg["tools"],
                upload_subject_id=actor.id,
            )
        )
        await session.flush()
        action = "mcp.create"
    else:
        dup = await session.execute(
            select(col(McpVersion.id)).where(
                col(McpVersion.mcp_id) == existing.id,
                col(McpVersion.version) == version,
            )
        )
        if dup.scalars().first() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="version_conflict"
            )
        existing.name = name
        existing.summary = summary
        existing.description = description
        if notes is not None:
            existing.notes = notes
        existing.latest_version = version
        existing.updated_at = utcnow()
        session.add(
            McpVersion(
                mcp_id=existing.id,
                version=version,
                transport=cfg["transport"],
                command=cfg["command"],
                args=cfg["args"],
                env=cfg["env"],
                url=cfg["url"],
                headers=cfg["headers"],
                tools=cfg["tools"],
                upload_subject_id=actor.id,
            )
        )
        await session.flush()
        mcp = existing
        action = "mcp.upload_version"

    await record_audit_event(
        session,
        action=action,
        resource_type="mcp",
        resource_id=mcp.id,
        outcome="success",
        actor_subject_id=actor.id,
        detail={
            "slug": slug,
            "version": version,
            "transport": cfg["transport"].value,
        },
    )
    return mcp


async def ensure_mcp_team_grant(
    session: AsyncSession, *, mcp_id: UUID, team_id: UUID
) -> McpTeamGrant:
    """Idempotent grant upsert: reactivate if exists, else create."""
    result = await session.execute(
        select(McpTeamGrant).where(
            col(McpTeamGrant.mcp_id) == mcp_id,
            col(McpTeamGrant.team_id) == team_id,
        )
    )
    grant = result.scalar_one_or_none()
    if grant:
        if grant.state != ResourceState.ACTIVE:
            grant.state = ResourceState.ACTIVE
            grant.updated_at = utcnow()
        return grant
    grant = McpTeamGrant(mcp_id=mcp_id, team_id=team_id)
    session.add(grant)
    await session.flush()
    return grant


async def list_visible_mcps(
    session: AsyncSession,
    *,
    subject_id: UUID,
    q: str | None = None,
    owner: str | None = None,
    limit: int = 30,
    offset: int = 0,
) -> tuple[list[MCP], int]:
    """Return mcps visible to subject_id (owner of OR team-granted), with search."""
    base_filter = [
        col(MCP.state) == ResourceState.ACTIVE,
        or_(
            col(MCP.owner_subject_id) == subject_id,
            col(MCP.id).in_(
                select(distinct(col(McpTeamGrant.mcp_id)))
                .join(Team, col(Team.id) == col(McpTeamGrant.team_id))
                .join(
                    TeamMembership,
                    col(TeamMembership.team_id) == col(Team.id),
                )
                .where(
                    col(McpTeamGrant.state) == ResourceState.ACTIVE,
                    col(Team.state) == ResourceState.ACTIVE,
                    col(TeamMembership.state) == ResourceState.ACTIVE,
                    col(TeamMembership.subject_id) == subject_id,
                )
            ),
        ),
    ]
    if q:
        needle = f"%{q}%"
        base_filter.append(
            or_(
                col(MCP.name).ilike(needle),
                col(MCP.summary).ilike(needle),
                col(MCP.slug).ilike(needle),
            )
        )
    if owner:
        base_filter.append(
            col(MCP.owner_subject_id).in_(
                select(col(Subject.id)).where(
                    or_(
                        col(Subject.login_username) == owner,
                        col(Subject.name) == owner,
                    )
                )
            )
        )
    count_stmt = select(func.count(distinct(col(MCP.id)))).where(*base_filter)
    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    list_stmt = (
        select(MCP)
        .where(*base_filter)
        .order_by(col(MCP.updated_at).desc())
        .limit(limit)
        .offset(offset)
    )
    items = list((await session.execute(list_stmt)).scalars().all())
    return items, total


async def get_mcp_version_row(
    session: AsyncSession, *, mcp_id: UUID, version: str
) -> McpVersion | None:
    stmt = select(McpVersion).where(
        col(McpVersion.mcp_id) == mcp_id,
        col(McpVersion.version) == version,
        col(McpVersion.state) == ResourceState.ACTIVE,
    )
    return (await session.execute(stmt)).scalars().first()


async def get_latest_active_mcp_version(
    session: AsyncSession, *, mcp: MCP
) -> McpVersion | None:
    """Resolve the latest_version pointer; fall back to most recent active by created_at."""
    if mcp.latest_version:
        pointed = await get_mcp_version_row(
            session, mcp_id=mcp.id, version=mcp.latest_version
        )
        if pointed:
            return pointed
    stmt = (
        select(McpVersion)
        .where(
            col(McpVersion.mcp_id) == mcp.id,
            col(McpVersion.state) == ResourceState.ACTIVE,
        )
        .order_by(col(McpVersion.created_at).desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()
