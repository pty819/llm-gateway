from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api.admin._common import StatePatch, _audit_update, _get_or_404
from llm_gateway.api.deps import session_dep
from llm_gateway.db.models import (
    MCP,
    McpTeamGrant,
    ResourceState,
    Skill,
    SkillTeamGrant,
    Team,
    utcnow,
)
from llm_gateway.services.resource_payloads import mcp_summary, skill_summary

router = APIRouter(prefix="/registry")


class SkillTeamGrantCreate(BaseModel):
    skill_id: UUID
    team_id: UUID


def _grant_dict(g: SkillTeamGrant) -> dict:
    return {
        "id": str(g.id),
        "skill_id": str(g.skill_id),
        "team_id": str(g.team_id),
        "state": g.state.value if hasattr(g.state, "value") else g.state,
    }


@router.get("/skill-team-grants")
async def list_skill_team_grants(session: AsyncSession = Depends(session_dep)):
    rows = (
        (
            await session.execute(
                select(SkillTeamGrant).order_by(col(SkillTeamGrant.created_at).desc())
            )
        )
        .scalars()
        .all()
    )
    items = [_grant_dict(g) for g in rows]
    return {"items": items, "total": len(items)}


@router.post("/skill-team-grants")
async def create_skill_team_grant(
    payload: SkillTeamGrantCreate, session: AsyncSession = Depends(session_dep)
):
    await _get_or_404(session, Skill, payload.skill_id)
    await _get_or_404(session, Team, payload.team_id)
    existing = (
        await session.execute(
            select(SkillTeamGrant).where(
                col(SkillTeamGrant.skill_id) == payload.skill_id,
                col(SkillTeamGrant.team_id) == payload.team_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        if existing.state != ResourceState.ACTIVE:
            existing.state = ResourceState.ACTIVE
            existing.updated_at = utcnow()
        grant = existing
    else:
        grant = SkillTeamGrant(skill_id=payload.skill_id, team_id=payload.team_id)
        session.add(grant)
        await session.flush()
    await _audit_update(
        session,
        action="skill_team_grant.create",
        resource_type="skill_team_grant",
        resource_id=grant.id,
        payload=payload,
    )
    await session.commit()
    await session.refresh(grant)
    return {"grant": _grant_dict(grant)}


@router.get("/skills/{skill_id}")
async def admin_get_skill(skill_id: UUID, session: AsyncSession = Depends(session_dep)):
    skill = await _get_or_404(session, Skill, skill_id)
    return {"skill": skill_summary(skill)}


@router.patch("/skills/{skill_id}/state")
async def admin_patch_skill_state(
    skill_id: UUID,
    payload: StatePatch,
    session: AsyncSession = Depends(session_dep),
):
    skill = await _get_or_404(session, Skill, skill_id)
    skill.state = payload.state
    skill.updated_at = utcnow()
    await _audit_update(
        session,
        action="skill.set_state",
        resource_type="skill",
        resource_id=skill.id,
        payload=payload,
    )
    await session.commit()
    await session.refresh(skill)
    return {"skill": skill_summary(skill)}


# ---- MCP super-admin ----


class McpTeamGrantCreate(BaseModel):
    mcp_id: UUID
    team_id: UUID


def _mcp_grant_dict(g: McpTeamGrant) -> dict:
    return {
        "id": str(g.id),
        "mcp_id": str(g.mcp_id),
        "team_id": str(g.team_id),
        "state": g.state.value if hasattr(g.state, "value") else g.state,
    }


@router.get("/mcp-team-grants")
async def list_mcp_team_grants(session: AsyncSession = Depends(session_dep)):
    rows = (
        (await session.execute(select(McpTeamGrant).order_by(col(McpTeamGrant.created_at).desc())))
        .scalars()
        .all()
    )
    items = [_mcp_grant_dict(g) for g in rows]
    return {"items": items, "total": len(items)}


@router.post("/mcp-team-grants")
async def create_mcp_team_grant(
    payload: McpTeamGrantCreate, session: AsyncSession = Depends(session_dep)
):
    await _get_or_404(session, MCP, payload.mcp_id)
    await _get_or_404(session, Team, payload.team_id)
    existing = (
        await session.execute(
            select(McpTeamGrant).where(
                col(McpTeamGrant.mcp_id) == payload.mcp_id,
                col(McpTeamGrant.team_id) == payload.team_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        if existing.state != ResourceState.ACTIVE:
            existing.state = ResourceState.ACTIVE
            existing.updated_at = utcnow()
        grant = existing
    else:
        grant = McpTeamGrant(mcp_id=payload.mcp_id, team_id=payload.team_id)
        session.add(grant)
        await session.flush()
    await _audit_update(
        session,
        action="mcp_team_grant.create",
        resource_type="mcp_team_grant",
        resource_id=grant.id,
        payload=payload,
    )
    await session.commit()
    await session.refresh(grant)
    return {"grant": _mcp_grant_dict(grant)}


@router.get("/mcps/{mcp_id}")
async def admin_get_mcp(mcp_id: UUID, session: AsyncSession = Depends(session_dep)):
    mcp = await _get_or_404(session, MCP, mcp_id)
    return {"mcp": mcp_summary(mcp)}


@router.patch("/mcps/{mcp_id}/state")
async def admin_patch_mcp_state(
    mcp_id: UUID,
    payload: StatePatch,
    session: AsyncSession = Depends(session_dep),
):
    mcp = await _get_or_404(session, MCP, mcp_id)
    mcp.state = payload.state
    mcp.updated_at = utcnow()
    await _audit_update(
        session,
        action="mcp.set_state",
        resource_type="mcp",
        resource_id=mcp.id,
        payload=payload,
    )
    await session.commit()
    await session.refresh(mcp)
    return {"mcp": mcp_summary(mcp)}
