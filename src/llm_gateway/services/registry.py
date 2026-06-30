from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.db.models import (
    ArtifactKind,
    ResourceState,
    Skill,
    SkillTeamGrant,
    SkillVersion,
    Subject,
    Team,
    TeamMembership,
    utcnow,
)


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
