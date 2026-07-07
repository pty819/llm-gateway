"""MCP protocol layer (Streamable HTTP transport) built on the official ``mcp`` SDK.

Exposes the registry functions as MCP tools, so agents can connect via MCP and
search/get/download skills + list/get MCPs. Uses ``FastMCP`` in stateless mode
(no session state — every request is self-contained) mounted under ``/v1/mcp``.

Authentication: gateway-key bearer, resolved in an ASGI middleware that wraps the
SDK's Starlette app. The resolved ``AuthContext`` is stashed on the ASGI scope
(``scope["state"]["auth"]``) and retrieved inside each tool via the MCP ``Context``.
"""

from __future__ import annotations

import contextlib
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.streamable_http_manager import TransportSecuritySettings
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse

from llm_gateway.api.registry import _get_visible_mcp_or_404, _get_visible_skill_or_404
from llm_gateway.services.registry import (
    build_mcp_detail_payload,
    build_skill_detail_payload,
    get_latest_active_version,
    get_skill_version,
    list_visible_mcps,
    list_visible_skills,
    resolve_owner_name_map,
)
from llm_gateway.services.resource_payloads import (
    mcp_summary,
    skill_summary,
)
from llm_gateway.services.security import AuthContext, authenticate_gateway_key

# Scope key under which the resolved AuthContext is stored by the auth middleware.
_AUTH_SCOPE_KEY = "mcp_auth_context"


async def _get_auth(ctx: Context) -> AuthContext:
    """Retrieve the gateway-key AuthContext stashed on the ASGI scope by the
    middleware. Every tool needs it to scope visibility to the calling subject."""
    request: StarletteRequest = ctx.request_context.request
    state = request.scope.get("state", {})
    auth = (
        state.get(_AUTH_SCOPE_KEY)
        if isinstance(state, dict)
        else getattr(state, _AUTH_SCOPE_KEY, None)
    )
    if auth is None:  # pragma: no cover — middleware rejects unauthenticated
        raise RuntimeError("MCP request reached a tool without an AuthContext")
    return auth


async def _get_session(ctx: Context) -> AsyncSession:
    """Open a fresh DB session for the tool call.

    The SDK's stateless transport runs outside FastAPI's dependency injection, so
    we cannot use ``Depends(session_dep)``. We open a session directly via the
    session factory; the caller is responsible for closing it.
    """
    from llm_gateway.db.session import AsyncSessionLocal

    return AsyncSessionLocal()


# ---------------------------------------------------------------------------
# FastMCP server + tool registration
# ---------------------------------------------------------------------------


mcp = FastMCP(
    name="llm-gateway-registry",
    instructions=(
        "Gateway registry: search, inspect, and download Skills; list and inspect "
        "MCP server configs. Visibility is scoped to your gateway key's subject "
        "(owned artifacts + those shared with your teams)."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    # Disable the SDK's built-in transport-security (host header / DNS rebinding
    # checks) — we run behind our own auth middleware and reverse proxy, and the
    # checks reject non-public hostnames like "testserver" or private IPs.
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)


@mcp.tool(description="Search skills visible to you (owned or shared with your teams).")
async def search_skills(
    q: str | None = None,
    owner: str | None = None,
    page: int = 1,
    size: int = 30,
    sort: str = "downloads",
    ctx: Context = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    auth = await _get_auth(ctx)
    session = await _get_session(ctx)
    try:
        offset = (page - 1) * size
        items, total = await list_visible_skills(
            session,
            subject_id=auth.subject.id,
            q=q,
            owner=owner,
            limit=size,
            offset=offset,
            sort=sort,
        )
        owner_names = await resolve_owner_name_map(session, {s.owner_subject_id for s in items})
        return {
            "items": [skill_summary(s, owner_names.get(s.owner_subject_id)) for s in items],
            "total": total,
            "page": page,
            "size": size,
        }
    finally:
        await session.close()


@mcp.tool(description="Get full detail of a skill including README and version history.")
async def get_skill(owner: str, slug: str, ctx: Context = None) -> dict[str, Any]:  # type: ignore[assignment]
    auth = await _get_auth(ctx)
    session = await _get_session(ctx)
    try:
        skill = await _get_visible_skill_or_404(
            session, owner_name=owner, slug=slug, subject_id=auth.subject.id
        )
        return await build_skill_detail_payload(session, skill, liked_by_me=False)
    finally:
        await session.close()


@mcp.tool(
    description=(
        "Get a download URL + checksum for a skill version. Use the returned URL "
        "with your gateway key as a Bearer token to download the zip over HTTP."
    )
)
async def download_skill(
    owner: str,
    slug: str,
    version: str = "latest",
    ctx: Context = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    auth = await _get_auth(ctx)
    session = await _get_session(ctx)
    try:
        skill = await _get_visible_skill_or_404(
            session, owner_name=owner, slug=slug, subject_id=auth.subject.id
        )
        if version == "latest":
            sv = await get_latest_active_version(session, skill=skill)
        else:
            sv = await get_skill_version(session, skill_id=skill.id, version=version)
        if sv is None:
            return {"error": "version_not_found"}
        request: StarletteRequest = ctx.request_context.request
        base = str(request.base_url).rstrip("/")
        url = f"{base}/v1/registry/skills/{owner}/{slug}/versions/{sv.version}/download"
        return {
            "url": url,
            "sha256": sv.content_sha256,
            "size_bytes": sv.size_bytes,
            "version": sv.version,
        }
    finally:
        await session.close()


@mcp.tool(description="List MCP server configs visible to you.")
async def list_mcps(
    q: str | None = None,
    owner: str | None = None,
    page: int = 1,
    size: int = 30,
    sort: str = "downloads",
    ctx: Context = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    auth = await _get_auth(ctx)
    session = await _get_session(ctx)
    try:
        offset = (page - 1) * size
        items, total = await list_visible_mcps(
            session,
            subject_id=auth.subject.id,
            q=q,
            owner=owner,
            limit=size,
            offset=offset,
            sort=sort,
        )
        owner_names = await resolve_owner_name_map(session, {m.owner_subject_id for m in items})
        return {
            "items": [mcp_summary(m, owner_names.get(m.owner_subject_id)) for m in items],
            "total": total,
            "page": page,
            "size": size,
        }
    finally:
        await session.close()


@mcp.tool(
    description=(
        "Get full detail of an MCP config. env/headers are redacted (shown as "
        "'***') unless you are the owner."
    )
)
async def get_mcp(owner: str, slug: str, ctx: Context = None) -> dict[str, Any]:  # type: ignore[assignment]
    auth = await _get_auth(ctx)
    session = await _get_session(ctx)
    try:
        mcp_obj = await _get_visible_mcp_or_404(
            session, owner_name=owner, slug=slug, subject_id=auth.subject.id
        )
        reveal = mcp_obj.owner_subject_id == auth.subject.id
        return await build_mcp_detail_payload(session, mcp_obj, reveal=reveal, liked_by_me=False)
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# ASGI app with gateway-key auth middleware
# ---------------------------------------------------------------------------


async def _auth_middleware(scope, receive, send):
    """ASGI middleware: resolve the gateway-key bearer, stash the AuthContext on
    the scope, then delegate to the MCP Starlette app. Rejects with 401 if the
    key is missing or invalid."""
    # Only intercept HTTP requests; pass through lifespan etc.
    if scope["type"] != "http":
        await _mcp_app(scope, receive, send)
        return

    from llm_gateway.db.session import AsyncSessionLocal

    auth_header = ""
    for key, value in scope.get("headers", []):
        if key == b"authorization":
            auth_header = value.decode("latin-1")
            break
        if key == b"x-api-key":
            auth_header = value.decode("latin-1")
            break

    raw_key: str | None = None
    if auth_header.lower().startswith("bearer "):
        raw_key = auth_header[7:].strip()
    elif auth_header:
        raw_key = auth_header.strip()

    if not raw_key:
        resp = JSONResponse(status_code=401, content={"detail": "missing_gateway_key"})
        await resp(scope, receive, send)
        return

    async with AsyncSessionLocal() as session:
        context = await authenticate_gateway_key(session, raw_key)
    if not context:
        resp = JSONResponse(status_code=401, content={"detail": "invalid_gateway_key"})
        await resp(scope, receive, send)
        return

    scope.setdefault("state", {})[_AUTH_SCOPE_KEY] = context
    await _ensure_session_manager()
    await _mcp_app(scope, receive, send)


# The SDK's Starlette app (lazy-initialized). We wrap it with our auth middleware.
# Disable redirect_slashes so POST /v1/mcp (no trailing slash) matches the "/"
# route exactly instead of 307-redirecting to /v1/mcp/.
_mcp_app = mcp.streamable_http_app()
_mcp_app.router.redirect_slashes = False


# The session manager's task group must be running for the SDK to handle
# requests. When the SDK app runs standalone its own lifespan starts it, but as
# a mounted sub-app FastAPI's lifespan governs. We start it eagerly in main.py's
# lifespan; for test contexts where the lifespan doesn't run, we also lazy-start
# on the first request.
_session_manager_started = False
_session_manager_ready: Any = None  # asyncio.Event, created lazily (needs loop)


async def _ensure_session_manager() -> None:
    """Start the MCP session manager task group if not already running.

    Idempotent: the SDK's run() can only be called once, so we guard with a flag.
    In production this is started by main.py's lifespan; this lazy path covers
    test ASGI transports that bypass lifespan. Waits until the task group is
    ready before returning so the first request doesn't race the startup.
    """
    global _session_manager_started, _session_manager_ready
    if _session_manager_started:
        if _session_manager_ready is not None:
            await _session_manager_ready.wait()
        return
    _session_manager_started = True
    import asyncio

    _session_manager_ready = asyncio.Event()

    async def _run_forever():
        async with mcp._session_manager.run():  # type: ignore[union-attr]
            _session_manager_ready.set()
            await asyncio.Event().wait()  # block until process exits

    asyncio.create_task(_run_forever())
    await _session_manager_ready.wait()


@contextlib.asynccontextmanager
async def mcp_lifespan():
    """Start/stop the MCP session manager task group (production lifespan)."""
    async with mcp._session_manager.run():  # type: ignore[union-attr]
        yield


def create_mcp_asgi_app():
    """Return the ASGI app (auth-wrapped) for mounting under FastAPI."""
    return _auth_middleware
