"""MCP protocol layer (Streamable HTTP transport, JSON-RPC 2.0).

Exposes the SAME registry functions as the ``/v1/registry/*`` data-plane as MCP
tools, so agents can connect via MCP and search/get/download skills + list/get
MCPs. The single ``POST /v1/mcp`` endpoint parses a JSON-RPC 2.0 request body
manually and dispatches to the corresponding tool handler.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api.deps import auth_dep, session_dep, settings_dep
from llm_gateway.api.registry import _get_visible_mcp_or_404, _get_visible_skill_or_404
from llm_gateway.core.config import Settings
from llm_gateway.db.models import (
    MCP,
    McpTeamGrant,
    McpVersion,
    ResourceState,
    Skill,
    SkillTeamGrant,
    SkillVersion,
    Subject,
)
from llm_gateway.services.registry import (
    get_latest_active_mcp_version,
    get_latest_active_version,
    get_skill_version,
    list_visible_mcps,
    list_visible_skills,
)
from llm_gateway.services.resource_payloads import (
    mcp_detail,
    mcp_summary,
    skill_detail,
    skill_summary,
)
from llm_gateway.services.security import AuthContext

router = APIRouter(prefix="/v1/mcp")


def _ok(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# Tool definitions (the MCP ``tools/list`` manifest)
# ---------------------------------------------------------------------------

_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "q": {"type": "string"},
        "owner": {"type": "string"},
        "page": {"type": "integer", "default": 1},
        "size": {"type": "integer", "default": 30},
        "sort": {"type": "string", "enum": ["downloads", "likes"], "default": "downloads"},
    },
}

_OWNER_SLUG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "owner": {"type": "string"},
        "slug": {"type": "string"},
    },
    "required": ["owner", "slug"],
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_skills",
        "description": "Search skills visible to you (owned or shared with your teams)",
        "inputSchema": _SEARCH_SCHEMA,
    },
    {
        "name": "get_skill",
        "description": "Get full detail of a skill including README and versions",
        "inputSchema": _OWNER_SLUG_SCHEMA,
    },
    {
        "name": "download_skill",
        "description": (
            "Get a download URL + checksum for a skill version. Use the URL with "
            "your gateway key as Bearer token to download the zip."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "slug": {"type": "string"},
                "version": {"type": "string", "default": "latest"},
            },
            "required": ["owner", "slug"],
        },
    },
    {
        "name": "list_mcps",
        "description": "List MCP server configs visible to you",
        "inputSchema": _SEARCH_SCHEMA,
    },
    {
        "name": "get_mcp",
        "description": "Get full detail of an MCP config (env/headers redacted if you're not the owner)",
        "inputSchema": _OWNER_SLUG_SCHEMA,
    },
]


# ---------------------------------------------------------------------------
# Tool call implementations
# ---------------------------------------------------------------------------


async def _resolve_owner_names(
    session: AsyncSession, items: list[Any]
) -> dict[Any, str]:
    """Map owner_subject_id -> Subject.name for the given items (like registry.py)."""
    owner_ids = {getattr(i, "owner_subject_id") for i in items}
    owner_names: dict[Any, str] = {}
    if owner_ids:
        rows = await session.execute(
            select(Subject.id, Subject.name).where(col(Subject.id).in_(owner_ids))
        )
        owner_names = {row[0]: row[1] for row in rows.all()}
    return owner_names


async def _tool_search_skills(
    arguments: dict[str, Any],
    *,
    auth: AuthContext,
    session: AsyncSession,
    request: Request,
) -> dict[str, Any]:
    page = int(arguments.get("page") or 1)
    size = int(arguments.get("size") or 30)
    offset = (page - 1) * size
    items, total = await list_visible_skills(
        session,
        subject_id=auth.subject.id,
        q=arguments.get("q"),
        owner=arguments.get("owner"),
        limit=size,
        offset=offset,
        sort=arguments.get("sort", "downloads"),
    )
    owner_names = await _resolve_owner_names(session, items)
    return {
        "items": [skill_summary(s, owner_names.get(s.owner_subject_id)) for s in items],
        "total": total,
        "page": page,
        "size": size,
    }


async def _tool_get_skill(
    arguments: dict[str, Any],
    *,
    auth: AuthContext,
    session: AsyncSession,
    request: Request,
) -> dict[str, Any]:
    owner = arguments["owner"]
    slug = arguments["slug"]
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
    owner_name = owner_obj.name if owner_obj else None
    return skill_detail(
        skill,
        versions,
        grants,
        owner_name=owner_name,
        readme=skill.readme,
        liked_by_me=False,
    )


async def _tool_download_skill(
    arguments: dict[str, Any],
    *,
    auth: AuthContext,
    session: AsyncSession,
    request: Request,
) -> dict[str, Any]:
    owner = arguments["owner"]
    slug = arguments["slug"]
    version = arguments.get("version", "latest")
    skill = await _get_visible_skill_or_404(
        session, owner_name=owner, slug=slug, subject_id=auth.subject.id
    )
    if version == "latest":
        sv = await get_latest_active_version(session, skill=skill)
    else:
        sv = await get_skill_version(session, skill_id=skill.id, version=version)
    if sv is None:
        raise HTTPException(status_code=404, detail="version_not_found")
    url = f"{request.base_url}v1/registry/skills/{owner}/{slug}/versions/{sv.version}/download"
    return {
        "url": url,
        "sha256": sv.content_sha256,
        "size_bytes": sv.size_bytes,
        "version": sv.version,
    }


async def _tool_list_mcps(
    arguments: dict[str, Any],
    *,
    auth: AuthContext,
    session: AsyncSession,
    request: Request,
) -> dict[str, Any]:
    page = int(arguments.get("page") or 1)
    size = int(arguments.get("size") or 30)
    offset = (page - 1) * size
    items, total = await list_visible_mcps(
        session,
        subject_id=auth.subject.id,
        q=arguments.get("q"),
        owner=arguments.get("owner"),
        limit=size,
        offset=offset,
        sort=arguments.get("sort", "downloads"),
    )
    owner_names = await _resolve_owner_names(session, items)
    return {
        "items": [mcp_summary(m, owner_names.get(m.owner_subject_id)) for m in items],
        "total": total,
        "page": page,
        "size": size,
    }


async def _tool_get_mcp(
    arguments: dict[str, Any],
    *,
    auth: AuthContext,
    session: AsyncSession,
    request: Request,
) -> dict[str, Any]:
    owner = arguments["owner"]
    slug = arguments["slug"]
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
    owner_name = owner_obj.name if owner_obj else None
    reveal = mcp.owner_subject_id == auth.subject.id
    return mcp_detail(
        mcp,
        versions,
        latest,
        grants,
        owner_name=owner_name,
        reveal=reveal,
        readme=mcp.readme,
        liked_by_me=False,
    )


_TOOL_DISPATCH: dict[str, Any] = {
    "search_skills": _tool_search_skills,
    "get_skill": _tool_get_skill,
    "download_skill": _tool_download_skill,
    "list_mcps": _tool_list_mcps,
    "get_mcp": _tool_get_mcp,
}


# ---------------------------------------------------------------------------
# JSON-RPC endpoint
# ---------------------------------------------------------------------------


@router.post("")
async def mcp_endpoint(
    request: Request,
    body: dict,
    auth: AuthContext = Depends(auth_dep),
    session: AsyncSession = Depends(session_dep),
    settings: Settings = Depends(settings_dep),
):
    if body.get("jsonrpc") != "2.0":
        return _err(body.get("id"), -32600, "invalid request: jsonrpc must be '2.0'")

    req_id = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}

    if method == "initialize":
        return _ok(
            req_id,
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "llm-gateway-registry", "version": "1.0"},
            },
        )

    if method == "ping":
        return _ok(req_id, {})

    if method == "tools/list":
        return _ok(req_id, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = _TOOL_DISPATCH.get(tool_name)
        if handler is None:
            return _err(req_id, -32601, "unknown tool")
        try:
            result = await handler(
                arguments, auth=auth, session=session, request=request
            )
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 — surface as JSON-RPC error
            return _err(req_id, -32603, str(e))
        return _ok(
            req_id,
            {
                "content": [
                    {"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}
                ]
            },
        )

    return _err(req_id, -32601, "method not found")
