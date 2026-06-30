# Marketplace MCP Registry — Slice 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an MCP marketplace to the LLM gateway, mirroring the Skill marketplace (Slice 1, already merged). An MCP artifact is a **connection config** (transport + command/url + args + env + headers + owner-declared tools list), NOT a zip. Users self-service-publish MCP configs and grant access to permission-groups (teams); agents fetch configs with their gateway key and connect to the MCP server themselves.

**Architecture:** Identical three-surface split as skills: `/v1/registry/mcps/*` (data-plane, gateway key), `/auth/registry/mcps/*` (self-service, session token), `/admin/registry/*` (super-admin, extended). Reuses Slice 1's owner-resolution, visibility, version-resolution, and grant-upsert patterns. **No DB work** — Slice 1 already created `mcps`, `mcp_versions`, `mcp_team_grants` tables and SQLModel entities. Only new service functions, routes, payload helpers (with env/headers redaction), and frontend.

**Tech Stack:** FastAPI + SQLModel/async SQLAlchemy + PostgreSQL + Redis + httpx ASGI test transport. Frontend: SvelteKit 2 / Svelte 5 runes.

**Spec:** `docs/superpowers/specs/2026-06-30-marketplace-skills-and-mcps-design.md` (§1.3 MCP entities, §1.4 grants, §2 API, §3.3 sensitive-field redaction)

**Slice 1 (already merged) provides the template.** Every MCP task is the structural twin of a Slice 1 skill task. `services/registry.py`, `api/registry.py`, `api/auth.py`, `api/admin/marketplace.py`, `services/resource_payloads.py` already exist and contain skill code to copy-and-adapt.

---

## Key differences from skills (read before starting)

1. **No upload file.** MCP publish is a JSON POST (`transport`, `command`, `args`, `env`, `url`, `headers`, `tools`, ...). No multipart, no `python-multipart` needed, no size limit.
2. **No download.** MCP configs are returned as JSON in the detail endpoint; there is no `download` route. The data-plane "get config" = the detail route.
3. **Sensitive-field redaction (NEW).** MCP `env` and `headers` may contain secrets. In the DATA-PLANE (`/v1/registry/mcps`) and in LIST responses, `env`/`headers` VALUES must be redacted to `"***"` (keep keys). The OWNER (via `/auth`) and ADMIN (via `/admin`) see cleartext. Implement a `redact_mcp_version(version, *, reveal: bool)` helper in `resource_payloads.py`. (Note: audit-event redaction of `env`/`headers` was already done in Slice 1 Task 4 by adding them to `_AUDIT_SENSITIVE_KEYS`; this task adds RESPONSE redaction, a separate concern.)
4. **MCPTransport enum.** Values `stdio`/`http`/`sse` (Python) map to PG enum `mcptransport` labels `STDIO`/`HTTP`/`SSE`. Validate the incoming transport string is one of these.
5. **`tools` is a JSONB list** of `{name, description, input_schema}` dicts, owner-declared. Store as-is; no validation of schema depth required for Slice 2.
6. **Visibility / grant / owner-resolution / version-resolution / latest-pointer / 404-existence-hiding** are ALL identical to skills — reuse the same patterns with MCP table/column names.

---

## File Structure

**Backend — modify (append MCP code alongside existing skill code):**
- `src/llm_gateway/services/registry.py` — append MCP service functions
- `src/llm_gateway/services/resource_payloads.py` — append `redact_mcp_version`, `mcp_summary`, `mcp_detail`
- `src/llm_gateway/api/registry.py` — append `/v1/registry/mcps/*` routes
- `src/llm_gateway/api/auth.py` — append `/auth/registry/mcps/*` routes
- `src/llm_gateway/api/admin/marketplace.py` — append `/admin/registry/mcp-team-grants` + mcp state routes
- `tests/test_marketplace_mcps.py` — NEW

**Frontend — modify:**
- `frontend/src/lib/api/types.ts` — +`Mcp*` types
- `frontend/src/lib/api/client.ts` — +mcp methods
- `frontend/src/lib/admin-config.ts` — +`mcp-market` section
- `frontend/src/lib/components/McpMarketSection.svelte` — NEW
- `frontend/src/lib/components/CreateMcpDialog.svelte` — NEW
- `frontend/src/routes/+page.svelte` — +`mcp-market` view branch

**No migration, no new DB table, no new config setting, no new dependency.**

---

## Task 1: MCP service layer (registry.py)

**Files:**
- Modify: `src/llm_gateway/services/registry.py` (append)
- Test: `tests/test_marketplace_mcps.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_marketplace_mcps.py`:

```python
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_user(login_username: str | None = None):
    from uuid import uuid4
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.db.models import Subject, SubjectType

    suffix = uuid4().hex
    async with AsyncSessionLocal() as session:
        subject = Subject(
            name=f"mcp-name-{suffix}",
            type=SubjectType.USER,
            login_username=login_username,
        )
        session.add(subject)
        await session.commit()
        await session.refresh(subject)
    return subject


def _unique_slug(base: str) -> str:
    """tests share a persistent DB; unique slugs avoid cross-test interference."""
    from uuid import uuid4
    return f"{base}-{uuid4().hex[:8]}"


def _mcp_config(*, transport="stdio", command="uvx mcp-server-x", url=None,
                args=None, env=None, headers=None, tools=None):
    return {
        "transport": transport,
        "command": command,
        "url": url,
        "args": args if args is not None else [],
        "env": env if env is not None else {"API_KEY": "secret-value"},
        "headers": headers if headers is not None else {},
        "tools": tools if tools is not None else [],
    }


async def test_create_mcp_creates_artifact_and_first_version():
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.db.models import McpVersion
    from llm_gateway.services.registry import create_or_append_mcp_version
    import sqlmodel

    owner = await _make_user()
    slug = _unique_slug("weather-mcp")
    async with AsyncSessionLocal() as session:
        mcp = await create_or_append_mcp_version(
            session, actor=owner, slug=slug, name="Weather MCP", version="1.0.0",
            summary="s", description=None, notes=None, config=_mcp_config(),
        )
        await session.commit()
        assert mcp.latest_version == "1.0.0"
        assert mcp.owner_subject_id == owner.id
        versions = (
            await session.execute(sqlmodel.select(McpVersion))
        ).scalars().all()
        assert len([v for v in versions if v.mcp_id == mcp.id]) == 1
        v = [v for v in versions if v.mcp_id == mcp.id][0]
        assert v.transport.value == "stdio"
        assert v.env == {"API_KEY": "secret-value"}


async def test_append_mcp_version_updates_latest():
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.registry import create_or_append_mcp_version

    owner = await _make_user()
    slug = _unique_slug("append")
    async with AsyncSessionLocal() as session:
        await create_or_append_mcp_version(
            session, actor=owner, slug=slug, name="M", version="1.0.0",
            summary=None, description=None, notes=None, config=_mcp_config(),
        )
        mcp = await create_or_append_mcp_version(
            session, actor=owner, slug=slug, name="M", version="2.0.0",
            summary=None, description=None, notes=None,
            config=_mcp_config(command="uvx mcp-server-x@2"),
        )
        await session.commit()
        assert mcp.latest_version == "2.0.0"


async def test_mcp_duplicate_version_raises_conflict():
    from fastapi import HTTPException
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.registry import create_or_append_mcp_version

    owner = await _make_user()
    slug = _unique_slug("dup")
    async with AsyncSessionLocal() as session:
        await create_or_append_mcp_version(
            session, actor=owner, slug=slug, name="D", version="1.0.0",
            summary=None, description=None, notes=None, config=_mcp_config(),
        )
        await session.commit()
    async with AsyncSessionLocal() as session:
        with pytest.raises(HTTPException) as exc:
            await create_or_append_mcp_version(
                session, actor=owner, slug=slug, name="D", version="1.0.0",
                summary=None, description=None, notes=None, config=_mcp_config(),
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "version_conflict"


async def test_mcp_cross_owner_slug_coexists():
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.registry import create_or_append_mcp_version

    alice = await _make_user()
    bob = await _make_user()
    slug = _unique_slug("shared")
    async with AsyncSessionLocal() as session:
        a = await create_or_append_mcp_version(
            session, actor=alice, slug=slug, name="A", version="1.0.0",
            summary=None, description=None, notes=None, config=_mcp_config(),
        )
        await session.commit()
    async with AsyncSessionLocal() as session:
        b = await create_or_append_mcp_version(
            session, actor=bob, slug=slug, name="B", version="1.0.0",
            summary=None, description=None, notes=None, config=_mcp_config(),
        )
        await session.commit()
    assert a.id != b.id
    assert a.owner_subject_id == alice.id and b.owner_subject_id == bob.id
    assert a.slug == b.slug == slug


async def test_mcp_grant_upsert_and_visibility():
    import uuid as _uuid
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.db.models import Team, TeamMembership
    from llm_gateway.services.registry import (
        create_or_append_mcp_version,
        ensure_mcp_team_grant,
        subject_can_access_mcp,
    )

    owner = await _make_user()
    consumer = await _make_user()
    slug = _unique_slug("grant")
    async with AsyncSessionLocal() as session:
        mcp = await create_or_append_mcp_version(
            session, actor=owner, slug=slug, name="G", version="1.0.0",
            summary=None, description=None, notes=None, config=_mcp_config(),
        )
        assert not await subject_can_access_mcp(session, subject_id=consumer.id, mcp=mcp)
        team = Team(name=f"mcp-team-{_uuid.uuid4().hex}")
        session.add(team)
        await session.flush()
        session.add(TeamMembership(team_id=team.id, subject_id=consumer.id))
        await session.flush()
        await ensure_mcp_team_grant(session, mcp_id=mcp.id, team_id=team.id)
        await session.commit()
        await session.refresh(mcp)
        assert await subject_can_access_mcp(session, subject_id=consumer.id, mcp=mcp)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_mcps.py -q`
Expected: FAIL — `ImportError: cannot import name 'create_or_append_mcp_version'`

- [ ] **Step 3: Implement the MCP service functions**

Append to `src/llm_gateway/services/registry.py`. First extend the model import block (lines ~11-21) to add `MCP`, `McpTeamGrant`, `McpVersion`, `MCPTransport`:

```python
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
```

Then append these functions at the end of the file:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_mcps.py -q`
Expected: all PASS (5 tests).

- [ ] **Step 5: Run the whole marketplace test set to confirm no skill regression**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_mcps.py tests/test_marketplace_skills.py -q`
Expected: all PASS (5 + 20).

- [ ] **Step 6: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add src/llm_gateway/services/registry.py tests/test_marketplace_mcps.py
git commit -m "Add registry service: MCP artifact (config) version mgmt + visibility + grants"
```

---

## Task 2: MCP payload helpers + redaction (resource_payloads.py)

**Files:**
- Modify: `src/llm_gateway/services/resource_payloads.py` (append)

- [ ] **Step 1: Append MCP payload helpers**

Append to `src/llm_gateway/services/resource_payloads.py`. The redaction helper replaces `env`/`headers` VALUES with `"***"` (keeps keys) when `reveal=False`. The `tools` list is never redacted (it's capability metadata, not secrets).

```python
_MCP_SENSITIVE_VERSION_KEYS = ("env", "headers")


def redact_mcp_version(version, *, reveal: bool = False) -> dict[str, Any]:
    """Serialize an McpVersion to a dict. env/headers values are replaced with
    '***' unless reveal=True (owner/admin only). tools are never redacted."""
    data = {
        "version": version.version,
        "transport": version.transport.value if hasattr(version.transport, "value") else version.transport,
        "command": version.command,
        "args": list(version.args or []),
        "env": dict(version.env or {}),
        "url": version.url,
        "headers": dict(version.headers or {}),
        "tools": list(version.tools or []),
        "upload_subject_id": str(version.upload_subject_id),
        "state": version.state.value if hasattr(version.state, "value") else version.state,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }
    if not reveal:
        for key in _MCP_SENSITIVE_VERSION_KEYS:
            data[key] = {k: "***" for k in (data[key] or {})}
    return data


def mcp_summary(mcp, owner_name: str | None = None) -> dict[str, Any]:
    return {
        "id": str(mcp.id),
        "owner_subject_id": str(mcp.owner_subject_id),
        "owner_name": owner_name,
        "slug": mcp.slug,
        "name": mcp.name,
        "summary": mcp.summary,
        "state": mcp.state.value if hasattr(mcp.state, "value") else mcp.state,
        "latest_version": mcp.latest_version,
        "updated_at": mcp.updated_at.isoformat() if mcp.updated_at else None,
    }


def mcp_detail(
    mcp, versions, latest_version, grants, owner_name: str | None = None,
    *, reveal: bool = False,
) -> dict[str, Any]:
    """versions are serialized with redaction per `reveal`. latest_version is the
    resolved latest McpVersion row (or None) also serialized with redaction."""
    detail = {
        **mcp_summary(mcp, owner_name=owner_name),
        "description": mcp.description,
        "notes": mcp.notes,
        "versions": [redact_mcp_version(v, reveal=reveal) for v in versions],
        "latest": redact_mcp_version(latest_version, reveal=reveal) if latest_version else None,
        "grants": [
            {
                "id": str(g.id),
                "mcp_id": str(g.mcp_id),
                "team_id": str(g.team_id),
                "state": g.state.value if hasattr(g.state, "value") else g.state,
            }
            for g in grants
        ],
    }
    return detail
```

- [ ] **Step 2: Verify it imports**

Run: `cd /Users/liyifan/llm_gateway && uv run python -c "from llm_gateway.services.resource_payloads import redact_mcp_version, mcp_summary, mcp_detail; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add src/llm_gateway/services/resource_payloads.py
git commit -m "Add MCP payload helpers with env/headers redaction"
```

---

## Task 3: Data-plane router `/v1/registry/mcps/*`

**Files:**
- Modify: `src/llm_gateway/api/registry.py` (append)
- Test: `tests/test_marketplace_mcps.py` (append)

- [ ] **Step 1: Write the failing tests (append to tests/test_marketplace_mcps.py)**

This reuses the `_login_user_with_key` helper from the skills test file — import it. Add a search helper and tests:

```python
from tests.test_marketplace_skills import _login_user_with_key
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.db.models import Subject, Team
from llm_gateway.services.registry import (
    create_or_append_mcp_version,
    ensure_mcp_team_grant,
)
from sqlmodel import select as sqlselect
from sqlmodel import col
from tests.test_backend_integration import _auth_headers


async def _publish_and_grant_to_guest(owner_id, slug):
    """Publish an MCP owned by owner_id and grant to the builtin guest team.
    Returns (mcp_id, slug)."""
    async with AsyncSessionLocal() as session:
        owner = await session.get(Subject, owner_id)
        mcp = await create_or_append_mcp_version(
            session, actor=owner, slug=slug, name="Weather MCP", version="1.0.0",
            summary="weather mcp", description=None, notes=None, config=_mcp_config(),
        )
        guest = (
            await session.execute(sqlselect(Team).where(col(Team.name) == "guest"))
        ).scalar_one()
        await ensure_mcp_team_grant(session, mcp_id=mcp.id, team_id=guest.id)
        await session.commit()
        return mcp.id, slug


async def test_dataplane_mcp_list_detail_and_redaction(client):
    _, _, username, owner_id = await _login_user_with_key(client)
    slug = _unique_slug("weather-mcp")
    await _publish_and_grant_to_guest(owner_id, slug)

    # a DIFFERENT registered user (also a guest member) can list + read detail
    _, other_gw, _, _ = await _login_user_with_key(client)
    resp = await client.get(
        f"/v1/registry/mcps?q={slug}", headers=_auth_headers(other_gw)
    )
    assert resp.status_code == 200, resp.text
    slugs = [m["slug"] for m in resp.json()["items"]]
    assert slug in slugs, slugs

    detail = await client.get(
        f"/v1/registry/mcps/{username}/{slug}", headers=_auth_headers(other_gw)
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["slug"] == slug
    # DATA-PLANE redaction: env/headers values must be "***"
    assert body["latest"]["env"] == {"API_KEY": "***"}, body["latest"]["env"]
    for v in body["versions"]:
        assert v["env"] == {"API_KEY": "***"}
    # transport + tools (non-secret) are visible
    assert body["latest"]["transport"] == "stdio"
    assert body["latest"]["command"] == "uvx mcp-server-x"


async def test_dataplane_hidden_mcp_returns_404(client):
    _, _, alice_user, alice_id = await _login_user_with_key(client)
    # alice publishes a PRIVATE mcp (no grant to anyone)
    async with AsyncSessionLocal() as session:
        alice = await session.get(Subject, alice_id)
        await create_or_append_mcp_version(
            session, actor=alice, slug=_unique_slug("private"), name="P", version="1.0.0",
            summary=None, description=None, notes=None, config=_mcp_config(),
        )
        await session.commit()
    _, other_gw, _, _ = await _login_user_with_key(client)
    # find alice's private slug by listing alice's own (owner) — use alice's key instead
    _, alice_gw, alice_login, _ = await _login_user_with_key(client)
    # stranger (other_gw) cannot see it -> 404, existence hidden
    resp = await client.get(
        f"/v1/registry/mcps/{alice_login}/nope-mcp", headers=_auth_headers(other_gw)
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "artifact_not_found"


async def test_dataplane_mcp_no_gateway_key_401(client):
    resp = await client.get("/v1/registry/mcps")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_mcps.py -q -k "dataplane"`
Expected: FAIL — routes 404.

- [ ] **Step 3: Append MCP routes to src/llm_gateway/api/registry.py**

Extend the existing imports to add the MCP service functions and models. The file currently imports `get_latest_active_version, get_skill_version, list_visible_skills, resolve_owner_subject, subject_can_access_skill` from services.registry and `skill_detail, skill_summary` from resource_payloads. Add the MCP counterparts. Append this code at the END of `src/llm_gateway/api/registry.py`:

```python
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
    # DATA-PLANE: reveal=False -> env/headers redacted
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
```

- [ ] **Step 4: Run the data-plane tests**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_mcps.py -q -k "dataplane"`
Expected: all PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add src/llm_gateway/api/registry.py tests/test_marketplace_mcps.py
git commit -m "Add data-plane /v1/registry/mcps router (list/detail with env/headers redaction)"
```

---

## Task 4: Self-service router `/auth/registry/mcps/*`

**Files:**
- Modify: `src/llm_gateway/api/auth.py` (append, after the existing skill routes ~line 1196)
- Test: `tests/test_marketplace_mcps.py` (append)

- [ ] **Step 1: Write the failing tests (append to tests/test_marketplace_mcps.py)**

```python
async def test_self_service_mcp_publish_and_reveal(client):
    sess_headers, gw_key, username, owner_id = await _login_user_with_key(client)
    slug = _unique_slug("my-mcp")
    cfg = _mcp_config(env={"SECRET": "top-secret"}, tools=[{"name": "get_weather"}])

    resp = await client.post(
        "/auth/registry/mcps", headers=sess_headers,
        json={
            "slug": slug, "name": "My MCP", "version": "1.0.0",
            "summary": "s", "config": cfg,
        },
    )
    assert resp.status_code == 200, resp.text
    mcp = resp.json()["mcp"]
    assert mcp["slug"] == slug
    assert mcp["latest_version"] == "1.0.0"

    # OWNER detail via data plane REVEALS cleartext env
    detail = await client.get(
        f"/v1/registry/mcps/{username}/{slug}", headers=_auth_headers(gw_key)
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["latest"]["env"] == {"SECRET": "top-secret"}
    assert detail.json()["latest"]["tools"] == [{"name": "get_weather"}]


async def test_self_service_mcp_append_version(client):
    sess_headers, *_ = await _login_user_with_key(client)
    slug = _unique_slug("append")
    await client.post(
        "/auth/registry/mcps", headers=sess_headers,
        json={"slug": slug, "name": "M", "version": "1.0.0", "config": _mcp_config()},
    )
    r2 = await client.post(
        "/auth/registry/mcps", headers=sess_headers,
        json={"slug": slug, "name": "M", "version": "2.0.0",
              "config": _mcp_config(command="uvx new@2")},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["mcp"]["latest_version"] == "2.0.0"


async def test_self_service_mcp_duplicate_version_409(client):
    sess_headers, *_ = await _login_user_with_key(client)
    slug = _unique_slug("dup")
    body = {"slug": slug, "name": "D", "version": "1.0.0", "config": _mcp_config()}
    r1 = await client.post("/auth/registry/mcps", headers=sess_headers, json=body)
    assert r1.status_code == 200
    r2 = await client.post("/auth/registry/mcps", headers=sess_headers, json=body)
    assert r2.status_code == 409
    assert r2.json()["detail"] == "version_conflict"


async def test_self_service_mcp_invalid_transport_422(client):
    sess_headers, *_ = await _login_user_with_key(client)
    r = await client.post(
        "/auth/registry/mcps", headers=sess_headers,
        json={"slug": _unique_slug("bad"), "name": "B", "version": "1.0.0",
              "config": _mcp_config(transport="bogus")},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "invalid_transport"


async def test_self_service_mcp_grants_lifecycle(client):
    sess_headers, gw_key, username, owner_id = await _login_user_with_key(client)
    slug = _unique_slug("grants")
    await client.post(
        "/auth/registry/mcps", headers=sess_headers,
        json={"slug": slug, "name": "G", "version": "1.0.0", "config": _mcp_config()},
    )
    async with AsyncSessionLocal() as session:
        guest = (
            await session.execute(sqlselect(Team).where(col(Team.name) == "guest"))
        ).scalar_one()
        guest_id = str(guest.id)

    r = await client.post(
        f"/auth/registry/mcps/me/{slug}/grants", headers=sess_headers,
        json={"team_id": guest_id},
    )
    assert r.status_code == 200, r.text
    grant_id = r.json()["grant"]["id"]

    g = await client.get(f"/auth/registry/mcps/me/{slug}/grants", headers=sess_headers)
    assert g.status_code == 200
    assert any(gr["id"] == grant_id for gr in g.json()["items"])

    rev = await client.patch(
        f"/auth/registry/mcps/me/{slug}/grants/{grant_id}/state",
        headers=sess_headers, json={"state": "disabled"},
    )
    assert rev.status_code == 200
    assert rev.json()["grant"]["state"] == "disabled"

    # after revoke, a guest member can no longer see the config
    _, other_gw, _, _ = await _login_user_with_key(client)
    detail = await client.get(
        f"/v1/registry/mcps/{username}/{slug}", headers=_auth_headers(other_gw)
    )
    assert detail.status_code == 404


async def test_self_service_mcp_non_owner_cannot_grant(client):
    alice_headers, *_ = await _login_user_with_key(client)
    bob_headers, *_ = await _login_user_with_key(client)
    slug = _unique_slug("owned")
    await client.post(
        "/auth/registry/mcps", headers=alice_headers,
        json={"slug": slug, "name": "A", "version": "1.0.0", "config": _mcp_config()},
    )
    async with AsyncSessionLocal() as session:
        guest = (
            await session.execute(sqlselect(Team).where(col(Team.name) == "guest"))
        ).scalar_one()
        guest_id = str(guest.id)
    r = await client.post(
        f"/auth/registry/mcps/me/{slug}/grants", headers=bob_headers,
        json={"team_id": guest_id},
    )
    assert r.status_code == 404  # 'me' resolves to bob who owns no such mcp
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_mcps.py -q -k "self_service"`
Expected: FAIL — routes 404.

- [ ] **Step 3: Append MCP self-service routes to src/llm_gateway/api/auth.py**

First extend auth.py's imports: the file already imports many things. Add (check for duplicates) `MCP`, `McpTeamGrant` to the db.models import, and `create_or_append_mcp_version`, `ensure_mcp_team_grant`, `get_mcp_by_owner_slug` to the services.registry import, and `mcp_summary` to the resource_payloads import. Then append at the END of the file (after the skill routes):

```python
# ---- marketplace: self-service MCP registry ----

class McpGrantCreate(BaseModel):
    team_id: UUID


@router.post("/registry/mcps")
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
    import re

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
    )
    await session.commit()
    await session.refresh(mcp)
    return {"mcp": mcp_summary(mcp, owner_name=ctx.subject.name)}


@router.get("/registry/mcps")
async def list_my_mcps(
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    from sqlalchemy import select as _select
    from sqlmodel import col as _col

    stmt = (
        _select(MCP)
        .where(_col(MCP.owner_subject_id) == ctx.subject.id)
        .order_by(_col(MCP.updated_at).desc())
    )
    items = list((await session.execute(stmt)).scalars().all())
    return {
        "items": [mcp_summary(m, owner_name=ctx.subject.name) for m in items],
        "total": len(items),
    }


async def _require_owned_mcp(session, ctx, slug, include_disabled=False):
    mcp = await get_mcp_by_owner_slug(
        session, owner_id=ctx.subject.id, slug=slug, include_disabled=include_disabled
    )
    if mcp is None or mcp.owner_subject_id != ctx.subject.id:
        raise HTTPException(status_code=404, detail="artifact_not_found")
    return mcp


@router.get("/registry/mcps/me/{slug}/grants")
async def list_my_mcp_grants(
    slug: str,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    mcp = await _require_owned_mcp(session, ctx, slug)
    from sqlalchemy import select as _select
    from sqlmodel import col as _col

    rows = (
        await session.execute(
            _select(McpTeamGrant).where(_col(McpTeamGrant.mcp_id) == mcp.id)
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


@router.post("/registry/mcps/me/{slug}/grants")
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


@router.patch("/registry/mcps/me/{slug}/grants/{grant_id}/state")
async def patch_my_mcp_grant_state(
    slug: str,
    grant_id: UUID,
    payload: dict,
    ctx=Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    from llm_gateway.db.models import ResourceState, utcnow

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
```

> **Owner reveal note:** the data-plane route `get_mcp_detail_route` was written in Task 3 with `reveal = (mcp.owner_subject_id == auth.subject.id)`, so owners already see cleartext and grantees see redacted. No further edit needed here. The test `test_self_service_mcp_publish_and_reveal` (below) verifies the owner sees cleartext via the data plane.

- [ ] **Step 4: Run the self-service tests**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_mcps.py -q -k "self_service"`
Expected: all PASS (6 tests).

- [ ] **Step 5: Run the whole MCP test file**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_mcps.py -q`
Expected: all PASS (5 + 3 + 6 = 14 tests).

- [ ] **Step 6: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add src/llm_gateway/api/auth.py src/llm_gateway/api/registry.py tests/test_marketplace_mcps.py
git commit -m "Add self-service /auth/registry/mcps routes + owner-reveal in data-plane"
```

---

## Task 5: Super-admin router extension `/admin/registry/mcp-team-grants` + mcp state

**Files:**
- Modify: `src/llm_gateway/api/admin/marketplace.py` (append)
- Test: `tests/test_marketplace_mcps.py` (append)

- [ ] **Step 1: Write the failing tests (append)**

Reuse `_admin_headers` from the skills test file (import it). Add:

```python
from tests.test_marketplace_skills import _admin_headers


async def test_admin_lists_mcp_team_grants(client):
    sess_headers, *_ = await _login_user_with_key(client)
    await client.post(
        "/auth/registry/mcps", headers=sess_headers,
        json={"slug": _unique_slug("adm"), "name": "A", "version": "1.0.0",
              "config": _mcp_config()},
    )
    admin = await _admin_headers(client)
    resp = await client.get("/admin/registry/mcp-team-grants", headers=admin)
    assert resp.status_code == 200, resp.text
    assert "items" in resp.json()


async def test_admin_can_disable_any_mcp(client):
    sess_headers, *_ = await _login_user_with_key(client)
    slug = _unique_slug("target")
    up = await client.post(
        "/auth/registry/mcps", headers=sess_headers,
        json={"slug": slug, "name": "T", "version": "1.0.0", "config": _mcp_config()},
    )
    mcp_id = up.json()["mcp"]["id"]
    admin = await _admin_headers(client)
    r = await client.patch(
        f"/admin/registry/mcps/{mcp_id}/state", headers=admin,
        json={"state": "disabled"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["mcp"]["state"] == "disabled"


async def test_admin_can_create_mcp_grant_for_any(client):
    sess_headers, *_ = await _login_user_with_key(client)
    slug = _unique_slug("grantable")
    up = await client.post(
        "/auth/registry/mcps", headers=sess_headers,
        json={"slug": slug, "name": "G", "version": "1.0.0", "config": _mcp_config()},
    )
    mcp_id = up.json()["mcp"]["id"]
    async with AsyncSessionLocal() as session:
        guest = (
            await session.execute(sqlselect(Team).where(col(Team.name) == "guest"))
        ).scalar_one()
        guest_id = str(guest.id)
    admin = await _admin_headers(client)
    r = await client.post(
        "/admin/registry/mcp-team-grants", headers=admin,
        json={"mcp_id": mcp_id, "team_id": guest_id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["grant"]["team_id"] == guest_id


async def test_admin_non_admin_user_forbidden_mcp(client):
    sess_headers, *_ = await _login_user_with_key(client)
    r = await client.get("/admin/registry/mcp-team-grants", headers=sess_headers)
    assert r.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_mcps.py -q -k "admin"`
Expected: FAIL — routes 404.

- [ ] **Step 3: Append MCP admin routes to src/llm_gateway/api/admin/marketplace.py**

Extend the existing imports in marketplace.py to add `MCP, McpTeamGrant` and `mcp_summary`. Then append at the END of the file:

```python
# ---- MCP super-admin ----

from llm_gateway.db.models import MCP, McpTeamGrant
from llm_gateway.services.resource_payloads import mcp_summary


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
        await session.execute(
            select(McpTeamGrant).order_by(col(McpTeamGrant.created_at).desc())
        )
    ).scalars().all()
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
    await session.commit()
    await session.refresh(mcp)
    return {"mcp": mcp_summary(mcp)}
```

- [ ] **Step 4: Run admin tests**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_mcps.py -q -k "admin"`
Expected: all PASS (4 tests).

- [ ] **Step 5: Run the whole MCP test file + skills (regression)**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_mcps.py tests/test_marketplace_skills.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add src/llm_gateway/api/admin/marketplace.py tests/test_marketplace_mcps.py
git commit -m "Add super-admin /admin/registry mcp-team-grants CRUD + mcp state"
```

---

## Task 6: Frontend — types + client + nav

**Files:**
- Modify: `frontend/src/lib/api/types.ts`
- Modify: `frontend/src/lib/api/client.ts`
- Modify: `frontend/src/lib/admin-config.ts`

- [ ] **Step 1: Add types to frontend/src/lib/api/types.ts (TABS)**

```typescript
export interface McpVersionDetail {
	version: string;
	transport: string;
	command: string | null;
	args: string[];
	env: Record<string, string>;
	url: string | null;
	headers: Record<string, string>;
	tools: Array<Record<string, unknown>>;
	upload_subject_id: string;
	state: string;
	created_at: string | null;
}

export interface McpTeamGrantSummary {
	id: string;
	mcp_id: string;
	team_id: string;
	state: string;
}

export interface McpSummary {
	id: string;
	owner_subject_id: string;
	owner_name: string | null;
	slug: string;
	name: string;
	summary: string | null;
	state: string;
	latest_version: string | null;
	updated_at: string | null;
}

export interface McpDetail extends McpSummary {
	description: string | null;
	notes: string | null;
	versions: McpVersionDetail[];
	latest: McpVersionDetail | null;
	grants: McpTeamGrantSummary[];
}

export interface McpConfigInput {
	transport: string;
	command?: string | null;
	args?: string[];
	env?: Record<string, string>;
	url?: string | null;
	headers?: Record<string, string>;
	tools?: Array<Record<string, unknown>>;
}
```

- [ ] **Step 2: Add client methods to AdminApiClient in frontend/src/lib/api/client.ts**

```typescript
	async listMyMcps(): Promise<Paginated<McpSummary>> {
		return this.get('/auth/registry/mcps');
	}

	async publishMcp(
		form: {
			slug: string;
			name: string;
			version: string;
			summary?: string;
			description?: string;
			notes?: string;
		},
		config: McpConfigInput
	): Promise<{ mcp: McpSummary }> {
		return this.post('/auth/registry/mcps', { ...form, config });
	}

	async listMcpGrants(slug: string): Promise<Paginated<McpTeamGrantSummary>> {
		return this.get(`/auth/registry/mcps/me/${encodeURIComponent(slug)}/grants`);
	}

	async grantMcp(slug: string, teamId: string): Promise<{ grant: McpTeamGrantSummary }> {
		return this.post(`/auth/registry/mcps/me/${encodeURIComponent(slug)}/grants`, {
			team_id: teamId
		});
	}

	async revokeMcpGrant(slug: string, grantId: string): Promise<{ grant: McpTeamGrantSummary }> {
		return this.patch(
			`/auth/registry/mcps/me/${encodeURIComponent(slug)}/grants/${grantId}/state`,
			{ state: 'disabled' }
		);
	}
```

Add `McpSummary`, `McpTeamGrantSummary`, `McpConfigInput` to the type import from `./types` if not present.

- [ ] **Step 3: Add nav section to frontend/src/lib/admin-config.ts**

Add `Plug` to the lucide-svelte import (alongside the existing `Package`). Add to the `sections` array right after the `skill-market` entry:

```typescript
	{ id: 'mcp-market', label: 'MCP 市场', group: '市场', icon: Plug },
```

- [ ] **Step 4: Type-check**

Run: `cd /Users/liyifan/llm_gateway/frontend && npm run check`
Expected: no type errors related to the new additions.

- [ ] **Step 5: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add frontend/src/lib/api/types.ts frontend/src/lib/api/client.ts frontend/src/lib/admin-config.ts
git commit -m "Add MCP marketplace frontend types, client methods, nav section"
```

---

## Task 7: Frontend — MCP market UI components

**Files:**
- Create: `frontend/src/lib/components/CreateMcpDialog.svelte`
- Create: `frontend/src/lib/components/McpMarketSection.svelte`
- Modify: `frontend/src/routes/+page.svelte`

- [ ] **Step 1: Read existing components first**

Read `frontend/src/lib/components/SkillMarketSection.svelte`, `UploadSkillDialog.svelte`, and `ArtifactGrantsEditor.svelte` (created in Slice 1) — mirror their structure, prop conventions (Svelte 5 runes, callback props for refresh), and styling. Read `+page.svelte` to find the view-switch and the `skill-market` branch you'll mirror for `mcp-market`.

- [ ] **Step 2: Create CreateMcpDialog.svelte**

A modal form: basic fields (slug validated against `marketSlugPattern`, name, version, summary), a transport `<select>` (stdio/http/sse) with dynamic fields:
- stdio: command (text), args (one string per line → split to array), env (key=value lines → dict)
- http/sse: url (text), headers (key=value lines → dict)
- tools (textarea, one tool name per line for Slice 2 simplicity → `[{name: line}]`)

On submit, build the `McpConfigInput` and call `client.publishMcp(form, config)`. Emit `close`/`published` via callback props (match `UploadSkillDialog`'s pattern).

- [ ] **Step 3: Create McpMarketSection.svelte**

Mirror `SkillMarketSection.svelte`: lists the caller's MCPs (slug/name/transport/latest/state) via `client.listMyMcps()`. "New MCP" button opens `CreateMcpDialog`. Clicking a row loads `client.listMcpGrants(slug)` and renders `ArtifactGrantsEditor` — but ArtifactGrantsEditor is skill-specific (calls `grantSkill`/`revokeSkillGrant`). **Two options:** (a) create a thin MCP-specific grants editor that calls `grantMcp`/`revokeMcpGrant`, or (b) generalize `ArtifactGrantsEditor` to accept `kind: 'skill' | 'mcp'` and dispatch accordingly. **Choose (a)** for Slice 2 (a focused `McpGrantsEditor.svelte`) to avoid touching the working skill component — copy `ArtifactGrantsEditor.svelte` and swap the client method names + the `skill_id`→`mcp_id` field. Keep the same `teams` prop + `onChanged` callback.

- [ ] **Step 4: Wire into +page.svelte**

Add a `{:else if active === 'mcp-market'}` branch (mirroring the `skill-market` branch) rendering `<McpMarketSection client={api} teams={inventory.teams} />`. Import the component at the top.

- [ ] **Step 5: Type-check + unit tests**

Run: `cd /Users/liyifan/llm_gateway/frontend && npm run check && npm run test`
Expected: type-check clean; existing unit tests pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add frontend/src/lib/components/CreateMcpDialog.svelte \
        frontend/src/lib/components/McpGrantsEditor.svelte \
        frontend/src/lib/components/McpMarketSection.svelte \
        frontend/src/routes/+page.svelte
git commit -m "Add MCP market UI: create dialog, grants editor, market section"
```

---

## Task 8: Final verification + README update

- [ ] **Step 1: Full backend test suite**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest -q`
Expected: all PASS (Slice 1 baseline + marketplace skills 20 + marketplace mcps ~18). Zero regression.

- [ ] **Step 2: Frontend checks**

Run: `cd /Users/liyifan/llm_gateway/frontend && npm run check && npm run test && npm run test:e2e`
Expected: all pass. e2e smoke still passes (it only checks the auth gate).

- [ ] **Step 3: Update README marketplace section**

In `README.md`, find the `## Marketplace（Skill 市场）` section and extend it to cover MCP. Rename the heading to `## Marketplace（Skill 与 MCP 市场）` and add an MCP paragraph:

```markdown
MCP 市场存放**连接配置**（非 zip）。用户上传 transport/command/url/args/env/headers/tools 配置：

    GET  /v1/registry/mcps                      # 可见 mcp 列表
    GET  /v1/registry/mcps/{owner}/{slug}       # 详情 + 当前配置（env/headers 在非 owner 视图下脱敏为 ***）

owner 自身可见明文 env/headers；被授权的权限组成员看到脱敏值。MCP 无 download 端点——
agent 拿到配置后自行连接 MCP server。
```

- [ ] **Step 4: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add README.md
git commit -m "Document MCP marketplace in README"
```

---

## Notes for the implementer

- **No DB/migration work** — all MCP tables exist from Slice 1.
- **Reveal logic is the subtle part.** Data-plane `/v1/registry/mcps/{owner}/{slug}` MUST set `reveal = (mcp.owner_subject_id == auth.subject.id)`. Owner sees cleartext; grantees + strangers see redacted env/headers. The admin route uses `mcp_summary` (no version details) so redaction there is moot; if you add an admin mcp-detail route later, default reveal=True.
- **Existence hiding:** invisible MCP → 404 `artifact_not_found` (same as skills), never 403.
- **Ownership via `me`:** self-service routes `/auth/registry/mcps/me/{slug}/*` resolve owner as `ctx.subject.id`, never trusting a path username.
- **Transport validation:** `_validate_mcp_config` rejects unknown transports with 422 `invalid_transport`; stdio requires `command`, http/sse requires `url`.
- **Reuse, don't duplicate:** `resolve_owner_subject` is already generic (works for any owner name) — do NOT make an MCP-specific copy. Only the visibility/version/grant functions are MCP-specific (different tables).
