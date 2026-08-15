from __future__ import annotations

import io
import re
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api.deps import (
    auth_dep,
    session_dep,
    settings_dep,
    user_session_dep,
)
from llm_gateway.core.config import Settings
from llm_gateway.db.models import (
    MCP,
    McpTeamGrant,
    ResourceState,
    Skill,
    SkillTeamGrant,
    Team,
    utcnow,
)
from llm_gateway.services.registry import (
    SLUG_PATTERN,
    assemble_mcp_detail,
    assemble_skill_detail,
    create_or_append_mcp_version,
    create_or_append_skill_version,
    ensure_mcp_team_grant,
    ensure_skill_team_grant,
    get_latest_active_version,
    get_mcp_by_owner_slug,
    get_skill_by_owner_slug,
    get_skill_version,
    get_visible_mcp_or_404,
    get_visible_skill_or_404,
    increment_skill_download_count,
    list_visible_mcps,
    list_visible_skills,
    resolve_owner_names,
    toggle_mcp_like,
    toggle_skill_like,
)
from llm_gateway.services.resource_payloads import mcp_summary, skill_summary
from llm_gateway.services.security import AuthContext

router = APIRouter(prefix="/v1/registry")


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
    owner_names = await resolve_owner_names(
        session, owner_ids={s.owner_subject_id for s in items}
    )
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
    skill = await get_visible_skill_or_404(
        session, owner_name=owner, slug=slug, subject_id=auth.subject.id
    )
    # The gateway-key detail route has never exposed readme/liked_by_me;
    # keep that exact response shape.
    return await assemble_skill_detail(
        session,
        skill=skill,
        viewer_subject_id=auth.subject.id,
        include_readme=False,
        include_likes=False,
    )


@router.get("/skills/{owner}/{slug}/versions/{version}/download")
async def download_skill_version_route(
    owner: str,
    slug: str,
    version: str,
    auth: AuthContext = Depends(auth_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await get_visible_skill_or_404(
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
    owner_names = await resolve_owner_names(
        session, owner_ids={m.owner_subject_id for m in items}
    )
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
    mcp = await get_visible_mcp_or_404(
        session, owner_name=owner, slug=slug, subject_id=auth.subject.id
    )
    # Exposes the README but never the caller's like state; the owner-scoped
    # reveal rule is applied inside assemble_mcp_detail.
    return await assemble_mcp_detail(
        session,
        mcp=mcp,
        viewer_subject_id=auth.subject.id,
        include_readme=True,
        include_likes=False,
    )


# ---------------------------------------------------------------------------
# Self-service marketplace under /auth/registry (session-cookie authenticated).
# Moved from api/auth.py so the marketplace code lives next to its gateway-key
# twin; the router prefix keeps every route path byte-for-byte identical.
# ---------------------------------------------------------------------------

auth_registry_router = APIRouter(prefix="/auth")


class SkillGrantCreate(BaseModel):
    team_id: UUID


class McpGrantCreate(BaseModel):
    team_id: UUID


@auth_registry_router.post("/registry/skills")
async def upload_skill(
    slug: str = Form(...),
    name: str = Form(...),
    version: str = Form(...),
    summary: str | None = Form(default=None),
    description: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    file: UploadFile = File(...),
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
    settings=Depends(settings_dep),
):
    if not re.match(SLUG_PATTERN, slug):
        raise HTTPException(status_code=422, detail="invalid_slug")
    zip_bytes = await file.read()
    if len(zip_bytes) > settings.marketplace_skill_max_bytes:
        raise HTTPException(status_code=413, detail="skill_too_large")
    if not zip_bytes:
        raise HTTPException(status_code=400, detail="empty_upload")
    skill = await create_or_append_skill_version(
        session,
        actor=ctx.subject,
        slug=slug,
        name=name,
        version=version,
        summary=summary,
        description=description,
        notes=notes,
        zip_bytes=zip_bytes,
    )
    await session.commit()
    await session.refresh(skill)
    return {"skill": skill_summary(skill, owner_name=ctx.subject.name)}


@auth_registry_router.get("/registry/skills")
async def list_my_skills(
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    stmt = (
        select(Skill)
        .where(col(Skill.owner_subject_id) == ctx.subject.id)
        .order_by(col(Skill.updated_at).desc())
    )
    items = list((await session.execute(stmt)).scalars().all())
    return {
        "items": [skill_summary(s, owner_name=ctx.subject.name) for s in items],
        "total": len(items),
    }


# ---- marketplace: browse (visible-to-me discovery) ----

@auth_registry_router.get("/registry/skills/browse")
async def browse_skills(
    q: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int | None = Query(default=None),
    sort: str = Query(default="downloads"),
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
    settings: Settings = Depends(settings_dep),
):
    page_size = size or settings.marketplace_list_default_size
    page_size = min(page_size, settings.marketplace_list_max_size)
    offset = (page - 1) * page_size
    items, total = await list_visible_skills(
        session,
        subject_id=ctx.subject.id,
        q=q,
        owner=owner,
        limit=page_size,
        offset=offset,
        sort=sort,
    )
    owner_names = await resolve_owner_names(
        session, owner_ids={s.owner_subject_id for s in items}
    )
    return {
        "items": [
            skill_summary(s, owner_names.get(s.owner_subject_id)) for s in items
        ],
        "total": total,
        "page": page,
        "size": page_size,
    }


@auth_registry_router.get("/registry/skills/browse/{owner}/{slug}")
async def browse_skill_detail(
    owner: str,
    slug: str,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await get_visible_skill_or_404(
        session, owner_name=owner, slug=slug, subject_id=ctx.subject.id
    )
    return await assemble_skill_detail(
        session, skill=skill, viewer_subject_id=ctx.subject.id
    )


@auth_registry_router.get("/registry/skills/browse/{owner}/{slug}/download")
async def browse_skill_download(
    owner: str,
    slug: str,
    version: str = Query(default="latest"),
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await get_visible_skill_or_404(
        session, owner_name=owner, slug=slug, subject_id=ctx.subject.id
    )
    if version == "latest":
        sv = await get_latest_active_version(session, skill=skill)
    else:
        sv = await get_skill_version(session, skill_id=skill.id, version=version)
    if sv is None:
        raise HTTPException(status_code=404, detail="version_not_found")
    await increment_skill_download_count(session, skill_id=skill.id)
    await session.commit()
    return StreamingResponse(
        io.BytesIO(sv.content_blob),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{slug}-{sv.version}.zip"',
            "X-Content-SHA256": sv.content_sha256,
            "ETag": f'"{sv.content_sha256}"',
        },
    )


@auth_registry_router.post("/registry/skills/browse/{owner}/{slug}/like")
async def browse_skill_like(
    owner: str,
    slug: str,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await get_visible_skill_or_404(
        session, owner_name=owner, slug=slug, subject_id=ctx.subject.id
    )
    skill = await toggle_skill_like(
        session, subject_id=ctx.subject.id, skill_id=skill.id
    )
    await session.commit()
    return {"liked_by_me": True, "like_count": skill.like_count}


@auth_registry_router.delete("/registry/skills/browse/{owner}/{slug}/like")
async def browse_skill_unlike(
    owner: str,
    slug: str,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await get_visible_skill_or_404(
        session, owner_name=owner, slug=slug, subject_id=ctx.subject.id
    )
    skill = await toggle_skill_like(
        session, subject_id=ctx.subject.id, skill_id=skill.id
    )
    await session.commit()
    return {"liked_by_me": False, "like_count": skill.like_count}


async def _require_owned_skill(session, ctx, slug, include_disabled=False):
    skill = await get_skill_by_owner_slug(
        session, owner_id=ctx.subject.id, slug=slug, include_disabled=include_disabled
    )
    if skill is None or skill.owner_subject_id != ctx.subject.id:
        raise HTTPException(status_code=404, detail="artifact_not_found")
    return skill


@auth_registry_router.get("/registry/skills/me/{slug}/grants")
async def list_my_skill_grants(
    slug: str,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await _require_owned_skill(session, ctx, slug)
    rows = (
        await session.execute(
            select(SkillTeamGrant).where(col(SkillTeamGrant.skill_id) == skill.id)
        )
    ).scalars().all()
    items = [
        {
            "id": str(g.id),
            "skill_id": str(g.skill_id),
            "team_id": str(g.team_id),
            "state": g.state.value if hasattr(g.state, "value") else g.state,
        }
        for g in rows
    ]
    return {"items": items, "total": len(items)}


@auth_registry_router.post("/registry/skills/me/{slug}/grants")
async def create_my_skill_grant(
    slug: str,
    payload: SkillGrantCreate,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await _require_owned_skill(session, ctx, slug)
    team = await session.get(Team, payload.team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="team_not_found")
    grant = await ensure_skill_team_grant(
        session, skill_id=skill.id, team_id=payload.team_id
    )
    await session.commit()
    await session.refresh(grant)
    return {
        "grant": {
            "id": str(grant.id),
            "skill_id": str(grant.skill_id),
            "team_id": str(grant.team_id),
            "state": grant.state.value if hasattr(grant.state, "value") else grant.state,
        }
    }


@auth_registry_router.patch("/registry/skills/me/{slug}/grants/{grant_id}/state")
async def patch_my_skill_grant_state(
    slug: str,
    grant_id: UUID,
    payload: dict,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await _require_owned_skill(session, ctx, slug)
    grant = await session.get(SkillTeamGrant, grant_id)
    if grant is None or grant.skill_id != skill.id:
        raise HTTPException(status_code=404, detail="grant_not_found")
    new_state = payload.get("state")
    if new_state not in ("active", "disabled"):
        raise HTTPException(status_code=422, detail="invalid_state")
    grant.state = ResourceState(new_state)
    grant.updated_at = utcnow()
    await session.commit()
    await session.refresh(grant)
    return {
        "grant": {
            "id": str(grant.id),
            "skill_id": str(grant.skill_id),
            "team_id": str(grant.team_id),
            "state": grant.state.value if hasattr(grant.state, "value") else grant.state,
        }
    }


# ---- self-service MCP registry ----

@auth_registry_router.post("/registry/mcps")
async def publish_mcp(
    payload: dict,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    slug = payload.get("slug")
    name = payload.get("name")
    version = payload.get("version")
    if not slug or not name or not version:
        raise HTTPException(status_code=422, detail="missing_required_field")
    if not re.match(SLUG_PATTERN, slug):
        raise HTTPException(status_code=422, detail="invalid_slug")
    mcp = await create_or_append_mcp_version(
        session,
        actor=ctx.subject,
        slug=slug,
        name=name,
        version=version,
        summary=payload.get("summary"),
        description=payload.get("description"),
        notes=payload.get("notes"),
        config=payload.get("config") or {},
        readme=payload.get("readme"),
    )
    await session.commit()
    await session.refresh(mcp)
    return {"mcp": mcp_summary(mcp, owner_name=ctx.subject.name)}


@auth_registry_router.get("/registry/mcps")
async def list_my_mcps(
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    stmt = (
        select(MCP)
        .where(col(MCP.owner_subject_id) == ctx.subject.id)
        .order_by(col(MCP.updated_at).desc())
    )
    items = list((await session.execute(stmt)).scalars().all())
    return {
        "items": [mcp_summary(m, owner_name=ctx.subject.name) for m in items],
        "total": len(items),
    }


# ---- marketplace: browse (visible-to-me discovery) ----

@auth_registry_router.get("/registry/mcps/browse")
async def browse_mcps(
    q: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int | None = Query(default=None),
    sort: str = Query(default="downloads"),
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
    settings: Settings = Depends(settings_dep),
):
    page_size = size or settings.marketplace_list_default_size
    page_size = min(page_size, settings.marketplace_list_max_size)
    offset = (page - 1) * page_size
    items, total = await list_visible_mcps(
        session,
        subject_id=ctx.subject.id,
        q=q,
        owner=owner,
        limit=page_size,
        offset=offset,
        sort=sort,
    )
    owner_names = await resolve_owner_names(
        session, owner_ids={m.owner_subject_id for m in items}
    )
    return {
        "items": [mcp_summary(m, owner_names.get(m.owner_subject_id)) for m in items],
        "total": total,
        "page": page,
        "size": page_size,
    }


@auth_registry_router.get("/registry/mcps/browse/{owner}/{slug}")
async def browse_mcp_detail(
    owner: str,
    slug: str,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    mcp = await get_visible_mcp_or_404(
        session, owner_name=owner, slug=slug, subject_id=ctx.subject.id
    )
    return await assemble_mcp_detail(
        session, mcp=mcp, viewer_subject_id=ctx.subject.id
    )


@auth_registry_router.post("/registry/mcps/browse/{owner}/{slug}/like")
async def browse_mcp_like(
    owner: str,
    slug: str,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    mcp = await get_visible_mcp_or_404(
        session, owner_name=owner, slug=slug, subject_id=ctx.subject.id
    )
    mcp = await toggle_mcp_like(
        session, subject_id=ctx.subject.id, mcp_id=mcp.id
    )
    await session.commit()
    return {"liked_by_me": True, "like_count": mcp.like_count}


@auth_registry_router.delete("/registry/mcps/browse/{owner}/{slug}/like")
async def browse_mcp_unlike(
    owner: str,
    slug: str,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    mcp = await get_visible_mcp_or_404(
        session, owner_name=owner, slug=slug, subject_id=ctx.subject.id
    )
    mcp = await toggle_mcp_like(
        session, subject_id=ctx.subject.id, mcp_id=mcp.id
    )
    await session.commit()
    return {"liked_by_me": False, "like_count": mcp.like_count}


async def _require_owned_mcp(session, ctx, slug, include_disabled=False):
    mcp = await get_mcp_by_owner_slug(
        session, owner_id=ctx.subject.id, slug=slug, include_disabled=include_disabled
    )
    if mcp is None or mcp.owner_subject_id != ctx.subject.id:
        raise HTTPException(status_code=404, detail="artifact_not_found")
    return mcp


@auth_registry_router.get("/registry/mcps/me/{slug}/grants")
async def list_my_mcp_grants(
    slug: str,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    mcp = await _require_owned_mcp(session, ctx, slug)
    rows = (
        await session.execute(
            select(McpTeamGrant).where(col(McpTeamGrant.mcp_id) == mcp.id)
        )
    ).scalars().all()
    items = [
        {
            "id": str(g.id),
            "mcp_id": str(g.mcp_id),
            "team_id": str(g.team_id),
            "state": g.state.value if hasattr(g.state, "value") else g.state,
        }
        for g in rows
    ]
    return {"items": items, "total": len(items)}


@auth_registry_router.post("/registry/mcps/me/{slug}/grants")
async def create_my_mcp_grant(
    slug: str,
    payload: McpGrantCreate,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    mcp = await _require_owned_mcp(session, ctx, slug)
    team = await session.get(Team, payload.team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="team_not_found")
    grant = await ensure_mcp_team_grant(
        session, mcp_id=mcp.id, team_id=payload.team_id
    )
    await session.commit()
    await session.refresh(grant)
    return {
        "grant": {
            "id": str(grant.id),
            "mcp_id": str(grant.mcp_id),
            "team_id": str(grant.team_id),
            "state": grant.state.value if hasattr(grant.state, "value") else grant.state,
        }
    }


@auth_registry_router.patch("/registry/mcps/me/{slug}/grants/{grant_id}/state")
async def patch_my_mcp_grant_state(
    slug: str,
    grant_id: UUID,
    payload: dict,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    mcp = await _require_owned_mcp(session, ctx, slug)
    grant = await session.get(McpTeamGrant, grant_id)
    if grant is None or grant.mcp_id != mcp.id:
        raise HTTPException(status_code=404, detail="grant_not_found")
    new_state = payload.get("state")
    if new_state not in ("active", "disabled"):
        raise HTTPException(status_code=422, detail="invalid_state")
    grant.state = ResourceState(new_state)
    grant.updated_at = utcnow()
    await session.commit()
    await session.refresh(grant)
    return {
        "grant": {
            "id": str(grant.id),
            "mcp_id": str(grant.mcp_id),
            "team_id": str(grant.team_id),
            "state": grant.state.value if hasattr(grant.state, "value") else grant.state,
        }
    }
