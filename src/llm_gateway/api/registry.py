from __future__ import annotations

import io
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api.deps import auth_dep, session_dep, settings_dep
from llm_gateway.core.config import Settings
from llm_gateway.db.models import ResourceState, Skill, SkillTeamGrant, SkillVersion, Subject
from llm_gateway.services.registry import (
    get_latest_active_version,
    get_skill_version,
    list_visible_skills,
    resolve_owner_subject,
    subject_can_access_skill,
)
from llm_gateway.services.resource_payloads import skill_detail, skill_summary
from llm_gateway.services.security import AuthContext

router = APIRouter(prefix="/v1/registry")


async def _get_visible_skill_or_404(
    session: AsyncSession, *, owner_name: str, slug: str, subject_id: UUID
) -> Skill:
    owner = await resolve_owner_subject(session, owner=owner_name)
    if owner is None:
        raise HTTPException(status_code=404, detail="artifact_not_found")
    stmt = select(Skill).where(
        col(Skill.owner_subject_id) == owner.id, col(Skill.slug) == slug
    )
    skill = (await session.execute(stmt)).scalars().first()
    if skill is None or skill.state != ResourceState.ACTIVE:
        raise HTTPException(status_code=404, detail="artifact_not_found")
    if not await subject_can_access_skill(session, subject_id=subject_id, skill=skill):
        raise HTTPException(status_code=404, detail="artifact_not_found")
    return skill


@router.get("/skills")
async def list_skills(
    q: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int | None = Query(default=None),
    auth: AuthContext = Depends(auth_dep),
    session: AsyncSession = Depends(session_dep),
    settings: Settings = Depends(settings_dep),
):
    page_size = size or settings.marketplace_list_default_size
    page_size = min(page_size, settings.marketplace_list_max_size)
    offset = (page - 1) * page_size
    items, total = await list_visible_skills(
        session,
        subject_id=auth.subject.id,
        q=q,
        owner=owner,
        limit=page_size,
        offset=offset,
    )
    owner_ids = {s.owner_subject_id for s in items}
    owner_names: dict[UUID, str] = {}
    if owner_ids:
        rows = await session.execute(
            select(Subject.id, Subject.name).where(col(Subject.id).in_(owner_ids))
        )
        owner_names = {row[0]: row[1] for row in rows.all()}
    return {
        "items": [skill_summary(s, owner_names.get(s.owner_subject_id)) for s in items],
        "total": total,
        "page": page,
        "size": page_size,
    }


@router.get("/skills/{owner}/{slug}")
async def get_skill_detail_route(
    owner: str,
    slug: str,
    auth: AuthContext = Depends(auth_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await _get_visible_skill_or_404(
        session, owner_name=owner, slug=slug, subject_id=auth.subject.id
    )
    versions = list(
        (
            await session.execute(
                select(SkillVersion)
                .where(
                    col(SkillVersion.skill_id) == skill.id,
                    col(SkillVersion.state) == ResourceState.ACTIVE,
                )
                .order_by(col(SkillVersion.created_at).desc())
            )
        ).scalars().all()
    )
    grants = list(
        (
            await session.execute(
                select(SkillTeamGrant).where(col(SkillTeamGrant.skill_id) == skill.id)
            )
        ).scalars().all()
    )
    owner_obj = await session.get(Subject, skill.owner_subject_id)
    return skill_detail(
        skill, versions, grants, owner_name=owner_obj.name if owner_obj else None
    )


@router.get("/skills/{owner}/{slug}/versions/{version}/download")
async def download_skill_version_route(
    owner: str,
    slug: str,
    version: str,
    auth: AuthContext = Depends(auth_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await _get_visible_skill_or_404(
        session, owner_name=owner, slug=slug, subject_id=auth.subject.id
    )
    if version == "latest":
        sv = await get_latest_active_version(session, skill=skill)
    else:
        sv = await get_skill_version(session, skill_id=skill.id, version=version)
    if sv is None:
        raise HTTPException(status_code=404, detail="version_not_found")
    return StreamingResponse(
        io.BytesIO(sv.content_blob),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{slug}-{sv.version}.zip"',
            "X-Content-SHA256": sv.content_sha256,
            "ETag": f'"{sv.content_sha256}"',
        },
    )


# ---- MCP data-plane ----

from llm_gateway.db.models import MCP, McpTeamGrant, McpVersion
from llm_gateway.services.registry import (
    get_latest_active_mcp_version,
    list_visible_mcps,
    subject_can_access_mcp,
)
from llm_gateway.services.resource_payloads import mcp_detail, mcp_summary


async def _get_visible_mcp_or_404(
    session: AsyncSession, *, owner_name: str, slug: str, subject_id: UUID
) -> MCP:
    owner = await resolve_owner_subject(session, owner=owner_name)
    if owner is None:
        raise HTTPException(status_code=404, detail="artifact_not_found")
    stmt = select(MCP).where(
        col(MCP.owner_subject_id) == owner.id, col(MCP.slug) == slug
    )
    mcp = (await session.execute(stmt)).scalars().first()
    if mcp is None or mcp.state != ResourceState.ACTIVE:
        raise HTTPException(status_code=404, detail="artifact_not_found")
    if not await subject_can_access_mcp(session, subject_id=subject_id, mcp=mcp):
        raise HTTPException(status_code=404, detail="artifact_not_found")
    return mcp


@router.get("/mcps")
async def list_mcps(
    q: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int | None = Query(default=None),
    auth: AuthContext = Depends(auth_dep),
    session: AsyncSession = Depends(session_dep),
    settings: Settings = Depends(settings_dep),
):
    page_size = size or settings.marketplace_list_default_size
    page_size = min(page_size, settings.marketplace_list_max_size)
    offset = (page - 1) * page_size
    items, total = await list_visible_mcps(
        session, subject_id=auth.subject.id, q=q, owner=owner,
        limit=page_size, offset=offset,
    )
    owner_ids = {m.owner_subject_id for m in items}
    owner_names: dict[UUID, str] = {}
    if owner_ids:
        rows = await session.execute(
            select(Subject.id, Subject.name).where(col(Subject.id).in_(owner_ids))
        )
        owner_names = {row[0]: row[1] for row in rows.all()}
    return {
        "items": [mcp_summary(m, owner_names.get(m.owner_subject_id)) for m in items],
        "total": total, "page": page, "size": page_size,
    }


@router.get("/mcps/{owner}/{slug}")
async def get_mcp_detail_route(
    owner: str,
    slug: str,
    auth: AuthContext = Depends(auth_dep),
    session: AsyncSession = Depends(session_dep),
):
    mcp = await _get_visible_mcp_or_404(
        session, owner_name=owner, slug=slug, subject_id=auth.subject.id
    )
    versions = list(
        (
            await session.execute(
                select(McpVersion)
                .where(
                    col(McpVersion.mcp_id) == mcp.id,
                    col(McpVersion.state) == ResourceState.ACTIVE,
                )
                .order_by(col(McpVersion.created_at).desc())
            )
        ).scalars().all()
    )
    grants = list(
        (
            await session.execute(
                select(McpTeamGrant).where(col(McpTeamGrant.mcp_id) == mcp.id)
            )
        ).scalars().all()
    )
    latest = await get_latest_active_mcp_version(session, mcp=mcp)
    owner_obj = await session.get(Subject, mcp.owner_subject_id)
    # Owner sees cleartext env/headers; grantees + strangers see redacted.
    reveal = mcp.owner_subject_id == auth.subject.id
    return mcp_detail(
        mcp, versions, latest, grants,
        owner_name=owner_obj.name if owner_obj else None, reveal=reveal,
    )
