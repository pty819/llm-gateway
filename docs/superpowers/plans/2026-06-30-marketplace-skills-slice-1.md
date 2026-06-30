# Marketplace Skills Registry — Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Skill marketplace to the LLM gateway — a pure-registry layer where any logged-in user uploads a skill zip (self-service, no review), controls which permission-groups (teams) may access it, and downstream agents browse/download with their gateway key.

**Architecture:** Pure registry (no runtime). Three API surfaces mirror the existing `/v1` (data-plane, gateway key), `/auth` (self-service, session token), `/admin` (super-admin) split. Access control reuses the existing `ModelTeamGrant` pattern via a new `SkillTeamGrant` table; "grant to the builtin `guest` team" == public because every user is a member of `guest`.

**Tech Stack:** FastAPI + SQLModel/async SQLAlchemy + PostgreSQL (BYTEA for zips) + Redis + httpx ASGI test transport. Frontend: SvelteKit 2 / Svelte 5 runes, hand-rolled components.

**Spec:** `docs/superpowers/specs/2026-06-30-marketplace-skills-and-mcps-design.md`

This plan covers **Slice 1 only** (Skill marketplace end-to-end). It also creates the 6 SQLModel entities and the full migration for all 6 tables (skill + mcp), so Slice 2 (MCP) needs no DB work later.

---

## File Structure

**Backend — create:**
- `alembic/versions/20260630_0011_marketplace_skills_and_mcps.py` — single migration, all 6 tables
- `src/llm_gateway/services/registry.py` — marketplace domain logic (owner resolution, visibility, version mgmt, grant upsert, redaction)
- `src/llm_gateway/api/registry.py` — data-plane router `/v1/registry/skills/*`
- `src/llm_gateway/api/admin/marketplace.py` — super-admin router `/admin/registry/skills/*` + `skill-team-grants`
- `tests/test_marketplace_skills.py` — Slice 1 integration tests

**Backend — modify:**
- `src/llm_gateway/db/models.py` — +6 entities + `ArtifactKind`/`MCPTransport` enums
- `src/llm_gateway/api/auth.py` — +self-service `/auth/registry/skills/*` routes
- `src/llm_gateway/api/admin/__init__.py` — register `marketplace` sub-router
- `src/llm_gateway/main.py` — register `registry` router
- `src/llm_gateway/services/resource_payloads.py` — +`paginated`/payload helpers already exist; add skill payload shape
- `src/llm_gateway/services/facts.py` — add marketplace-sensitive keys to `_AUDIT_SENSITIVE_KEYS`
- `src/llm_gateway/core/config.py` — +3 marketplace settings
- `.env.example` — +3 documented settings

**Frontend — create:**
- `frontend/src/lib/components/SkillMarketSection.svelte`
- `frontend/src/lib/components/UploadSkillDialog.svelte`
- `frontend/src/lib/components/ArtifactGrantsEditor.svelte`

**Frontend — modify:**
- `frontend/src/lib/admin-config.ts` — +`skill-market` section + `marketSlugPattern` helper
- `frontend/src/lib/api/types.ts` — +`Skill*` types
- `frontend/src/lib/api/client.ts` — +marketplace methods
- `frontend/src/routes/+page.svelte` — +view branch for `skill-market`

---

## Task 1: SQLModel entities + enums

**Files:**
- Modify: `src/llm_gateway/db/models.py` (append enums after line 49; append 6 classes after the `Skill`/`MCP`-adjacent region, near end of file before `AuditEvent`)

- [ ] **Step 1: Add the two enums**

Open `src/llm_gateway/db/models.py`. After the `UsageSource` enum (line 49), add:

```python
class ArtifactKind(StrEnum):
    SKILL = "skill"
    MCP = "mcp"


class MCPTransport(StrEnum):
    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"
```

- [ ] **Step 2: Add the 6 entity classes**

Append these before the `class AuditEvent` definition (keeps marketplace tables grouped together). `TimestampMixin`, `ResourceState`, `Column`, `JSONB`, `Field`, `uuid4`, `UUID`, `Any` are already imported at the top of the file — no new imports needed.

```python
class Skill(TimestampMixin, table=True):
    __tablename__ = "skills"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    owner_subject_id: UUID = Field(foreign_key="subjects.id", index=True)
    slug: str = Field(index=True)
    name: str
    summary: str | None = None
    description: str | None = None
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)
    latest_version: str | None = Field(default=None, index=True)
    notes: str | None = None


class SkillVersion(TimestampMixin, table=True):
    __tablename__ = "skill_versions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    skill_id: UUID = Field(foreign_key="skills.id", index=True)
    version: str = Field(index=True)
    content_blob: bytes
    content_sha256: str = Field(index=True)
    size_bytes: int
    upload_subject_id: UUID = Field(foreign_key="subjects.id")
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)


class MCP(TimestampMixin, table=True):
    __tablename__ = "mcps"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    owner_subject_id: UUID = Field(foreign_key="subjects.id", index=True)
    slug: str = Field(index=True)
    name: str
    summary: str | None = None
    description: str | None = None
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)
    latest_version: str | None = Field(default=None, index=True)
    notes: str | None = None


class McpVersion(TimestampMixin, table=True):
    __tablename__ = "mcp_versions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    mcp_id: UUID = Field(foreign_key="mcps.id", index=True)
    version: str = Field(index=True)
    transport: MCPTransport = Field(index=True)
    command: str | None = None
    args: list[str] = Field(default_factory=list, sa_column=Column(JSONB))
    env: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSONB))
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSONB))
    tools: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB))
    upload_subject_id: UUID = Field(foreign_key="subjects.id")
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)


class SkillTeamGrant(TimestampMixin, table=True):
    __tablename__ = "skill_team_grants"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    skill_id: UUID = Field(foreign_key="skills.id", index=True)
    team_id: UUID = Field(foreign_key="teams.id", index=True)
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)


class McpTeamGrant(TimestampMixin, table=True):
    __tablename__ = "mcp_team_grants"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    mcp_id: UUID = Field(foreign_key="mcps.id", index=True)
    team_id: UUID = Field(foreign_key="teams.id", index=True)
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)
```

- [ ] **Step 3: Verify the module imports cleanly**

Run: `cd /Users/liyifan/llm_gateway && uv run python -c "from llm_gateway.db.models import Skill, SkillVersion, MCP, McpVersion, SkillTeamGrant, McpTeamGrant, ArtifactKind, MCPTransport; print('ok')"`
Expected: prints `ok`, no errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add src/llm_gateway/db/models.py
git commit -m "Add Skill/MCP marketplace SQLModel entities and enums"
```

---

## Task 2: Alembic migration (all 6 tables)

**Files:**
- Create: `alembic/versions/20260630_0011_marketplace_skills_and_mcps.py`

- [ ] **Step 1: Write the migration**

Create the file. It mirrors the shape of `alembic/versions/20260525_0002_auth_teams.py`: reuse the existing `resourcestate` ENUM with `create_type=False`, use `sqlmodel.sql.sqltypes.AutoString()` for text columns, `sa.Uuid()` for UUIDs, `postgresql.JSONB` for JSON columns, `sa.LargeBinary()` for the BYTEA blob. Unique constraints follow the `uq_<table>_<cols>` naming; indexes follow `ix_<table>_<col>`.

```python
"""Add marketplace skills and mcps tables.

Revision ID: 20260630_0011
Revises: 20260629_0010
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
import sqlmodel.sql.sqltypes
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260630_0011"
down_revision: str | None = "20260629_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    resource_state = postgresql.ENUM(
        "ACTIVE", "DISABLED", name="resourcestate", create_type=False
    )
    mcp_transport = postgresql.ENUM(
        "STDIO", "HTTP", "SSE", name="mcptransport", create_type=False
    )

    # --- skills ---
    op.create_table(
        "skills",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_subject_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("state", resource_state, nullable=False),
        sa.Column("latest_version", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("notes", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["owner_subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_subject_id", "slug", name="uq_skill_owner_slug"),
    )
    op.create_index("ix_skills_owner_subject_id", "skills", ["owner_subject_id"])
    op.create_index("ix_skills_slug", "skills", ["slug"])
    op.create_index("ix_skills_state", "skills", ["state"])
    op.create_index("ix_skills_latest_version", "skills", ["latest_version"])

    # --- skill_versions ---
    op.create_table(
        "skill_versions",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("content_blob", sa.LargeBinary(), nullable=False),
        sa.Column("content_sha256", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("upload_subject_id", sa.Uuid(), nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"]),
        sa.ForeignKeyConstraint(["upload_subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("skill_id", "version", name="uq_skill_version_skill_version"),
    )
    op.create_index("ix_skill_versions_skill_id", "skill_versions", ["skill_id"])
    op.create_index("ix_skill_versions_version", "skill_versions", ["version"])
    op.create_index("ix_skill_versions_content_sha256", "skill_versions", ["content_sha256"])
    op.create_index("ix_skill_versions_state", "skill_versions", ["state"])

    # --- mcps ---
    op.create_table(
        "mcps",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_subject_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("state", resource_state, nullable=False),
        sa.Column("latest_version", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("notes", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["owner_subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_subject_id", "slug", name="uq_mcp_owner_slug"),
    )
    op.create_index("ix_mcps_owner_subject_id", "mcps", ["owner_subject_id"])
    op.create_index("ix_mcps_slug", "mcps", ["slug"])
    op.create_index("ix_mcps_state", "mcps", ["state"])
    op.create_index("ix_mcps_latest_version", "mcps", ["latest_version"])

    # --- mcp_versions ---
    op.create_table(
        "mcp_versions",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("mcp_id", sa.Uuid(), nullable=False),
        sa.Column("version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("transport", mcp_transport, nullable=False),
        sa.Column("command", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("args", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("env", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("headers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tools", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("upload_subject_id", sa.Uuid(), nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.ForeignKeyConstraint(["mcp_id"], ["mcps.id"]),
        sa.ForeignKeyConstraint(["upload_subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mcp_id", "version", name="uq_mcp_version_mcp_version"),
    )
    op.create_index("ix_mcp_versions_mcp_id", "mcp_versions", ["mcp_id"])
    op.create_index("ix_mcp_versions_version", "mcp_versions", ["version"])
    op.create_index("ix_mcp_versions_transport", "mcp_versions", ["transport"])
    op.create_index("ix_mcp_versions_state", "mcp_versions", ["state"])

    # --- skill_team_grants ---
    op.create_table(
        "skill_team_grants",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("skill_id", "team_id", name="uq_skill_team_grant_skill_team"),
    )
    op.create_index("ix_skill_team_grants_skill_id", "skill_team_grants", ["skill_id"])
    op.create_index("ix_skill_team_grants_team_id", "skill_team_grants", ["team_id"])
    op.create_index("ix_skill_team_grants_state", "skill_team_grants", ["state"])

    # --- mcp_team_grants ---
    op.create_table(
        "mcp_team_grants",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("mcp_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.ForeignKeyConstraint(["mcp_id"], ["mcps.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mcp_id", "team_id", name="uq_mcp_team_grant_mcp_team"),
    )
    op.create_index("ix_mcp_team_grants_mcp_id", "mcp_team_grants", ["mcp_id"])
    op.create_index("ix_mcp_team_grants_team_id", "mcp_team_grants", ["team_id"])
    op.create_index("ix_mcp_team_grants_state", "mcp_team_grants", ["state"])


def downgrade() -> None:
    for table in (
        "mcp_team_grants",
        "skill_team_grants",
        "mcp_versions",
        "mcps",
        "skill_versions",
        "skills",
    ):
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_state")
        op.drop_table(table)
```

> **Note on the `mcptransport` ENUM:** the model uses `MCPTransport(StrEnum)` with values `"stdio"/"http"/"sse"`, but PostgreSQL ENUM names are uppercased by SQLAlchemy's `Enum`/`StrEnum` handling (matching how `resourcestate` stores `ACTIVE`/`DISABLED` for the `ResourceState` enum). The migration declares `mcp_transport` with the uppercased names. SQLModel's `StrEnum` field maps `MCPTransport.STDIO` ("stdio") to the DB enum label "STDIO" automatically, consistent with how `ResourceState.ACTIVE` already works in this codebase. If `op.create_index("ix_*_state", ...)` collision arises in downgrade for shared names, the `DROP INDEX IF EXISTS` + `drop_table` ordering above is safe.

- [ ] **Step 2: Apply the migration**

Run: `cd /Users/liyifan/llm_gateway && uv run python scripts/init_db.py`
Expected: completes without error; `alembic upgrade head` stamps revision `20260630_0011`.

- [ ] **Step 3: Verify all 6 tables exist**

Run:
```bash
cd /Users/liyifan/llm_gateway && uv run python -c "
import asyncio
from sqlalchemy import inspect
from llm_gateway.db.session import engine
async def main():
    names = (await engine.run_sync(lambda sync_engine: inspect(sync_engine).get_table_names()))
    for t in ['skills','skill_versions','mcps','mcp_versions','skill_team_grants','mcp_team_grants']:
        assert t in names, f'missing {t}'
    print('all 6 tables present')
asyncio.run(main())
"
```
Expected: prints `all 6 tables present`.

- [ ] **Step 4: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add alembic/versions/20260630_0011_marketplace_skills_and_mcps.py
git commit -m "Add migration 0011: marketplace skills and mcps tables"
```

---

## Task 3: Config settings + env docs

**Files:**
- Modify: `src/llm_gateway/core/config.py` (append 3 fields to `Settings`, near `session_ttl_hours` ~line 80)
- Modify: `.env.example`

- [ ] **Step 1: Add 3 settings to `Settings`**

In `src/llm_gateway/core/config.py`, after the `session_ttl_hours` field, add:

```python
    marketplace_skill_max_bytes: int = Field(
        default=10 * 1024 * 1024, alias="LLM_GATEWAY_MARKETPLACE_SKILL_MAX_BYTES"
    )
    marketplace_list_default_size: int = Field(
        default=30, alias="LLM_GATEWAY_MARKETPLACE_LIST_DEFAULT_SIZE"
    )
    marketplace_list_max_size: int = Field(
        default=100, alias="LLM_GATEWAY_MARKETPLACE_LIST_MAX_SIZE"
    )
```

- [ ] **Step 2: Document in `.env.example`**

Append to `.env.example`:

```
# Marketplace: max skill zip upload size in bytes (default 10 MiB)
# LLM_GATEWAY_MARKETPLACE_SKILL_MAX_BYTES=10485760
# Marketplace: default page size for registry list endpoints (default 30)
# LLM_GATEWAY_MARKETPLACE_LIST_DEFAULT_SIZE=30
# Marketplace: max page size for registry list endpoints (default 100)
# LLM_GATEWAY_MARKETPLACE_LIST_MAX_SIZE=100
```

- [ ] **Step 3: Verify settings load**

Run: `cd /Users/liyifan/llm_gateway && uv run python -c "from llm_gateway.core.config import get_settings; s=get_settings(); print(s.marketplace_skill_max_bytes, s.marketplace_list_default_size, s.marketplace_list_max_size)"`
Expected: `10485760 30 100`

- [ ] **Step 4: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add src/llm_gateway/core/config.py .env.example
git commit -m "Add marketplace config settings"
```

---

## Task 4: Audit sensitive keys

**Files:**
- Modify: `src/llm_gateway/services/facts.py:183-198` (the `_AUDIT_SENSITIVE_KEYS` frozenset)

- [ ] **Step 1: Add marketplace-sensitive key names**

In `src/llm_gateway/services/facts.py`, add three keys to the `_AUDIT_SENSITIVE_KEYS` frozenset (these are JSONB field names / header keys that MCP config may carry in Slice 2; adding them now keeps the audit-redaction contract complete):

```python
_AUDIT_SENSITIVE_KEYS = frozenset(
    {
        "api_key_value",
        "api_key_ref",
        "password",
        "token_hash",
        "key_hash",
        "authorization",
        "x-api-key",
        "api-key",
        "apikey",
        "bearer",
        "cookie",
        "secret",
        "env",
        "headers",
    }
)
```

- [ ] **Step 2: Verify redaction still works**

Run: `cd /Users/liyifan/llm_gateway && uv run python -c "from llm_gateway.services.facts import _redact_audit_detail; print(_redact_audit_detail({'env':{'K':'V'}, 'slug':'ok'}))"`
Expected: `{'env': '<redacted>', 'slug': 'ok'}`

- [ ] **Step 3: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add src/llm_gateway/services/facts.py
git commit -m "Add env/headers to audit-sensitive keys for marketplace"
```

---

## Task 5: Service layer — owner resolution + visibility

**Files:**
- Create: `src/llm_gateway/services/registry.py`
- Test: `tests/test_marketplace_skills.py` (create)

- [ ] **Step 1: Write the failing test for owner resolution**

Create `tests/test_marketplace_skills.py`:

```python
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_resolve_owner_by_login_username_then_name():
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.db.models import Subject, SubjectType
    from uuid import uuid4

    suffix = uuid4().hex
    async with AsyncSessionLocal() as session:
        subject = Subject(
            name=f"display-{suffix}",
            type=SubjectType.USER,
            login_username=f"l{(uuid4().int % 100_000_000):08d}",
        )
        session.add(subject)
        await session.commit()
        await session.refresh(subject)

    from llm_gateway.services.registry import resolve_owner_subject

    async with AsyncSessionLocal() as session:
        by_username = await resolve_owner_subject(session, owner=subject.login_username)
        assert by_username is not None and by_username.id == subject.id
        by_name = await resolve_owner_subject(session, owner=subject.name)
        assert by_name is not None and by_name.id == subject.id
        missing = await resolve_owner_subject(session, owner="does-not-exist-xyz")
        assert missing is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_skills.py::test_resolve_owner_by_login_username_then_name -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_gateway.services.registry'`

- [ ] **Step 3: Create the service module with owner resolution**

Create `src/llm_gateway/services/registry.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_skills.py::test_resolve_owner_by_login_username_then_name -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add src/llm_gateway/services/registry.py tests/test_marketplace_skills.py
git commit -m "Add registry service: owner resolution and skill visibility"
```

---

## Task 6: Service layer — skill version upload/append + grant upsert

**Files:**
- Modify: `src/llm_gateway/services/registry.py` (append functions)
- Test: `tests/test_marketplace_skills.py` (append)

- [ ] **Step 1: Write the failing test for upload + append + conflict**

Append to `tests/test_marketplace_skills.py`:

```python
import io
import zipfile

from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.db.models import Subject, SubjectType, Team, TeamMembership
from llm_gateway.services.registry import (
    create_or_append_skill_version,
    ensure_skill_team_grant,
    subject_can_access_skill,
)


def _make_zip(text: str = "SKILL.md content") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SKILL.md", text)
    return buf.getvalue()


async def _make_user(login_username: str | None = None) -> Subject:
    from uuid import uuid4

    suffix = uuid4().hex
    async with AsyncSessionLocal() as session:
        subject = Subject(
            name=f"name-{suffix}",
            type=SubjectType.USER,
            login_username=login_username,
        )
        session.add(subject)
        await session.commit()
        await session.refresh(subject)
    return subject


async def test_upload_creates_skill_and_first_version():
    owner = await _make_user()
    async with AsyncSessionLocal() as session:
        skill = await create_or_append_skill_version(
            session,
            actor=owner,
            slug="weather",
            name="Weather",
            version="1.0.0",
            summary="s",
            description=None,
            notes=None,
            zip_bytes=_make_zip(),
        )
        await session.commit()
        assert skill.latest_version == "1.0.0"
        assert skill.owner_subject_id == owner.id
        from llm_gateway.db.models import SkillVersion

        versions = (
            await session.execute(__import__("sqlmodel").select(SkillVersion))
        ).scalars().all()
        assert len(versions) == 1
        assert versions[0].size_bytes > 0
        assert len(versions[0].content_sha256) == 64


async def test_upload_append_new_version_updates_latest():
    owner = await _make_user()
    async with AsyncSessionLocal() as session:
        await create_or_append_skill_version(
            session, actor=owner, slug="x", name="X", version="1.0.0",
            summary=None, description=None, notes=None, zip_bytes=_make_zip("v1"),
        )
        skill = await create_or_append_skill_version(
            session, actor=owner, slug="x", name="X", version="2.0.0",
            summary=None, description=None, notes=None, zip_bytes=_make_zip("v2"),
        )
        await session.commit()
        assert skill.latest_version == "2.0.0"


async def test_upload_duplicate_version_raises_conflict():
    from fastapi import HTTPException

    owner = await _make_user()
    async with AsyncSessionLocal() as session:
        await create_or_append_skill_version(
            session, actor=owner, slug="x", name="X", version="1.0.0",
            summary=None, description=None, notes=None, zip_bytes=_make_zip(),
        )
        await session.commit()
    async with AsyncSessionLocal() as session:
        with pytest.raises(HTTPException) as exc:
            await create_or_append_skill_version(
                session, actor=owner, slug="x", name="X", version="1.0.0",
                summary=None, description=None, notes=None, zip_bytes=_make_zip(),
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "version_conflict"


async def test_upload_slug_taken_by_other_owner_raises_conflict():
    from fastapi import HTTPException

    alice = await _make_user()
    bob = await _make_user()
    async with AsyncSessionLocal() as session:
        await create_or_append_skill_version(
            session, actor=alice, slug="weather", name="W", version="1.0.0",
            summary=None, description=None, notes=None, zip_bytes=_make_zip(),
        )
        await session.commit()
    async with AsyncSessionLocal() as session:
        with pytest.raises(HTTPException) as exc:
            await create_or_append_skill_version(
                session, actor=bob, slug="weather", name="W", version="1.0.0",
                summary=None, description=None, notes=None, zip_bytes=_make_zip(),
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "artifact_slug_conflict"


async def test_grant_upsert_and_visibility():
    owner = await _make_user()
    consumer = await _make_user()
    async with AsyncSessionLocal() as session:
        skill = await create_or_append_skill_version(
            session, actor=owner, slug="s", name="S", version="1.0.0",
            summary=None, description=None, notes=None, zip_bytes=_make_zip(),
        )
        # private by default: consumer cannot see
        assert not await subject_can_access_skill(
            session, subject_id=consumer.id, skill=skill
        )
        # create a team, add consumer, grant skill to team
        team = Team(name=f"team-{__import__('uuid').uuid4().hex}")
        session.add(team)
        await session.flush()
        session.add(TeamMembership(team_id=team.id, subject_id=consumer.id))
        await session.flush()
        await ensure_skill_team_grant(
            session, skill_id=skill.id, team_id=team.id
        )
        await session.commit()
        await session.refresh(skill)
        assert await subject_can_access_skill(
            session, subject_id=consumer.id, skill=skill
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_skills.py -q -k "upload or grant_upsert"`
Expected: FAIL — `ImportError: cannot import name 'create_or_append_skill_version'`

- [ ] **Step 3: Implement upload/append + grant upsert**

Append to `src/llm_gateway/services/registry.py`:

```python
from fastapi import HTTPException, status

from llm_gateway.db.models import Skill, SkillVersion
from llm_gateway.services.facts import record_audit_event


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

    If (actor, slug) does not exist -> create the skill + first version.
    If it exists and actor is the owner -> append a new version, make it latest.
    If it exists but owner is someone else -> 409 artifact_slug_conflict.
    Duplicate version string on the same skill -> 409 version_conflict.
    """
    existing = await get_skill_by_owner_slug(
        session, owner_id=actor.id, slug=slug, include_disabled=True
    )
    if existing is not None and existing.owner_subject_id != actor.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="artifact_slug_conflict",
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
        # reject duplicate version string on this skill
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_skills.py -q -k "upload or grant_upsert"`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add src/llm_gateway/services/registry.py tests/test_marketplace_skills.py
git commit -m "Add registry service: skill version upload/append and grant upsert"
```

---

## Task 7: List-visible-skills + version resolution service

**Files:**
- Modify: `src/llm_gateway/services/registry.py` (append)
- Test: `tests/test_marketplace_skills.py` (append)

- [ ] **Step 1: Write the failing test for list visibility + guest==public**

Append to `tests/test_marketplace_skills.py`:

```python
async def test_list_visible_guest_grant_equals_public():
    """A skill granted to the builtin 'guest' team is visible to every subject
    that is a guest member (i.e. everyone)."""
    owner = await _make_user()
    consumer = await _make_user()

    # make consumer a member of builtin 'guest' team
    async with AsyncSessionLocal() as session:
        from sqlmodel import select as sqlselect

        guest = (
            await session.execute(sqlselect(Team).where(col(Team.name) == "guest"))
        ).scalar_one()
        session.add(TeamMembership(team_id=guest.id, subject_id=consumer.id))
        await session.commit()

    async with AsyncSessionLocal() as session:
        skill = await create_or_append_skill_version(
            session, actor=owner, slug="pub", name="Pub", version="1.0.0",
            summary=None, description=None, notes=None, zip_bytes=_make_zip(),
        )
        await ensure_skill_team_grant(
            session, skill_id=skill.id, team_id=guest.id
        )
        await session.commit()

    from llm_gateway.services.registry import list_visible_skills

    async with AsyncSessionLocal() as session:
        items, total = await list_visible_skills(session, subject_id=consumer.id)
        slugs = [s.slug for s in items]
        assert "pub" in slugs
        assert total >= 1


async def test_list_visible_excludes_unauthorized():
    owner = await _make_user()
    stranger = await _make_user()
    async with AsyncSessionLocal() as session:
        await create_or_append_skill_version(
            session, actor=owner, slug="secret", name="Secret", version="1.0.0",
            summary=None, description=None, notes=None, zip_bytes=_make_zip(),
        )
        await session.commit()
    from llm_gateway.services.registry import list_visible_skills

    async with AsyncSessionLocal() as session:
        items, _ = await list_visible_skills(session, subject_id=stranger.id)
        assert all(s.slug != "secret" for s in items)
        # but owner sees their own
        items_owner, _ = await list_visible_skills(session, subject_id=owner.id)
        assert any(s.slug == "secret" for s in items_owner)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_skills.py -q -k "list_visible"`
Expected: FAIL — `ImportError: cannot import name 'list_visible_skills'`

- [ ] **Step 3: Implement list_visible_skills + version resolution helpers**

Append to `src/llm_gateway/services/registry.py`. Note: add `func` and `distinct` to the existing sqlalchemy import line at the top of the file (change `from sqlalchemy import or_, select` to `from sqlalchemy import distinct, func, or_, select`).

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_skills.py -q -k "list_visible"`
Expected: all PASS.

- [ ] **Step 5: Run the whole marketplace test file so far**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_skills.py -q`
Expected: all PASS (8 tests).

- [ ] **Step 6: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add src/llm_gateway/services/registry.py tests/test_marketplace_skills.py
git commit -m "Add registry service: list_visible_skills and version resolution"
```

---

## Task 8: Payload helpers for skills

**Files:**
- Modify: `src/llm_gateway/services/resource_payloads.py` (append)

- [ ] **Step 1: Add skill payload + owner-name enrichment helpers**

Append to `src/llm_gateway/services/resource_payloads.py`:

```python
def skill_summary(skill, owner_name: str | None = None) -> dict[str, Any]:
    return {
        "id": str(skill.id),
        "owner_subject_id": str(skill.owner_subject_id),
        "owner_name": owner_name,
        "slug": skill.slug,
        "name": skill.name,
        "summary": skill.summary,
        "state": skill.state.value if hasattr(skill.state, "value") else skill.state,
        "latest_version": skill.latest_version,
        "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
    }


def skill_detail(skill, versions, grants, owner_name: str | None = None) -> dict[str, Any]:
    return {
        **skill_summary(skill, owner_name=owner_name),
        "description": skill.description,
        "notes": skill.notes,
        "versions": [
            {
                "version": v.version,
                "content_sha256": v.content_sha256,
                "size_bytes": v.size_bytes,
                "upload_subject_id": str(v.upload_subject_id),
                "state": v.state.value if hasattr(v.state, "value") else v.state,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in versions
        ],
        "grants": [
            {
                "id": str(g.id),
                "skill_id": str(g.skill_id),
                "team_id": str(g.team_id),
                "state": g.state.value if hasattr(g.state, "value") else g.state,
            }
            for g in grants
        ],
    }
```

- [ ] **Step 2: Verify it imports**

Run: `cd /Users/liyifan/llm_gateway && uv run python -c "from llm_gateway.services.resource_payloads import skill_summary, skill_detail; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add src/llm_gateway/services/resource_payloads.py
git commit -m "Add skill payload helpers"
```

---

## Task 9: Data-plane router `/v1/registry/skills/*`

**Files:**
- Create: `src/llm_gateway/api/registry.py`
- Modify: `src/llm_gateway/main.py` (register router)
- Test: `tests/test_marketplace_skills.py` (append)

- [ ] **Step 1: Write failing tests for data-plane list/detail/download**

Append to `tests/test_marketplace_skills.py`. These tests exercise the HTTP layer via the existing `client` fixture and the self-service user login helper (mirroring `tests/test_self_key_management.py`).

```python
async def _login_user_with_key(client):
    """Register a fresh user, return (session_headers, raw_gateway_key, username)."""
    from tests.test_backend_integration import _employee_username, _auth_headers
    from llm_gateway.core.config import get_settings
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.security import (
        create_gateway_key,
        create_registered_user,
        create_user_session,
    )

    username = _employee_username()
    async with AsyncSessionLocal() as session:
        subject, project, _key, _raw = await create_registered_user(
            session,
            username=username,
            full_name="市场用户",
            password="correct-horse-battery",
        )
        user_session, raw_session = await create_user_session(
            session,
            subject_id=subject.id,
            ttl_hours=get_settings().session_ttl_hours,
        )
        gw_key, raw_gw = await create_gateway_key(
            session, subject_id=subject.id, project_id=project.id, name="mk",
        )
        await session.commit()
    return (
        {"x-session-token": raw_session},
        raw_gw,
        username,
        subject.id,
    )


async def test_dataplane_list_and_download_for_guest_grant(client):
    from tests.test_backend_integration import _auth_headers
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.db.models import Team
    from llm_gateway.services.registry import (
        create_or_append_skill_version,
        ensure_skill_team_grant,
    )
    from sqlmodel import select as sqlselect
    from sqlmodel import col as _col

    sess_headers, gw_key, username, owner_id = await _login_user_with_key(client)

    # upload a skill as owner, grant to guest
    async with AsyncSessionLocal() as session:
        skill = await create_or_append_skill_version(
            session,
            actor=await session.get(Subject, owner_id),
            slug="weather",
            name="Weather",
            version="1.0.0",
            summary="weather skill",
            description=None,
            notes=None,
            zip_bytes=_make_zip(),
        )
        guest = (
            await session.execute(sqlselect(Team).where(_col(Team.name) == "guest"))
        ).scalar_one()
        await ensure_skill_team_grant(session, skill_id=skill.id, team_id=guest.id)
        await session.commit()

    # a DIFFERENT registered user (also a guest member) can list + download
    _, other_gw, _, _ = await _login_user_with_key(client)
    resp = await client.get(
        "/v1/registry/skills?q=weather", headers=_auth_headers(other_gw)
    )
    assert resp.status_code == 200, resp.text
    slugs = [s["slug"] for s in resp.json()["items"]]
    assert "weather" in slugs

    # detail
    detail = await client.get(
        f"/v1/registry/skills/{username}/weather", headers=_auth_headers(other_gw)
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["slug"] == "weather"

    # download latest
    dl = await client.get(
        f"/v1/registry/skills/{username}/weather/versions/latest/download",
        headers=_auth_headers(other_gw),
    )
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/zip"
    assert dl.content[:2] == b"PK"  # zip magic


async def test_dataplane_hidden_skill_returns_404(client):
    from tests.test_backend_integration import _auth_headers

    _, owner_gw, username, _ = await _login_user_with_key(client)
    _, other_gw, _, _ = await _login_user_with_key(client)

    # owner uploads a private skill (no grant) directly via the self-service API
    resp = await client.post(
        "/auth/registry/skills",
        headers=owner_gw if False else {**_auth_headers(""), "x-session-token": ""},
        data={"slug": "secret", "name": "Secret", "version": "1.0.0"},
        files={"file": ("secret.zip", _make_zip(), "application/zip")},
    )
    # (the self-service upload route is wired in Task 10; if not yet present this
    #  test will fail at the upload step until then — that's expected TDD ordering.
    #  Keep this test; it turns green once Task 10 lands.)
    assert resp.status_code in (200, 404), resp.text
```

> The second test (`test_dataplane_hidden_skill_returns_404`) intentionally depends on the self-service upload route from Task 10. It is written here so the hidden-404 contract is captured early; it will fully pass after Task 10. Run the first test now; run both after Task 10.

- [ ] **Step 2: Run the first data-plane test to verify it fails**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_skills.py::test_dataplane_list_and_download_for_guest_grant -q`
Expected: FAIL — route `/v1/registry/skills` returns 404 (router not registered).

- [ ] **Step 3: Create the data-plane router**

Create `src/llm_gateway/api/registry.py`:

```python
from __future__ import annotations

import io
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api.deps import auth_dep, session_dep, settings_dep
from llm_gateway.core.config import Settings
from llm_gateway.db.models import ResourceState, Subject, Skill, SkillTeamGrant
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


def _resolve_owner_or_404(session: AsyncSession, owner: str) -> Subject:
    raise NotImplementedError  # replaced below


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
async def get_skill_detail(
    owner: str,
    slug: str,
    auth: AuthContext = Depends(auth_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await _get_visible_skill_or_404(
        session, owner_name=owner, slug=slug, subject_id=auth.subject.id
    )
    from llm_gateway.db.models import SkillVersion

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
    owner = await session.get(Subject, skill.owner_subject_id)
    return skill_detail(
        skill, versions, grants, owner_name=owner.name if owner else None
    )


@router.get("/skills/{owner}/{slug}/versions/{version}/download")
async def download_skill_version(
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
```

Now **delete** the placeholder `_resolve_owner_or_404` function block (lines defining it with `raise NotImplementedError`) — it is unused; `_get_visible_skill_or_404` calls `resolve_owner_subject` directly.

- [ ] **Step 4: Register the router in main.py**

In `src/llm_gateway/main.py`, add the import and registration:

```python
from llm_gateway.api import admin, auth, health, proxy, realtime, registry
```
(extend the existing import line), and inside `create_app()`, after `app.include_router(proxy.router)`:

```python
    app.include_router(registry.router)
```

- [ ] **Step 5: Run the first data-plane test to verify it passes**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_skills.py::test_dataplane_list_and_download_for_guest_grant -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add src/llm_gateway/api/registry.py src/llm_gateway/main.py tests/test_marketplace_skills.py
git commit -m "Add data-plane /v1/registry/skills router (list/detail/download)"
```

---

## Task 10: Self-service router `/auth/registry/skills/*`

**Files:**
- Modify: `src/llm_gateway/api/auth.py` (append routes; the `router = APIRouter(prefix="/auth")` already exists at line 58)
- Test: `tests/test_marketplace_skills.py` (append)

- [ ] **Step 1: Write failing tests for self-service upload + ownership + grants**

Append to `tests/test_marketplace_skills.py`:

```python
async def test_self_service_upload_and_download_lifecycle(client):
    sess_headers, gw_key, username, owner_id = await _login_user_with_key(client)

    # upload
    resp = await client.post(
        "/auth/registry/skills",
        headers=sess_headers,
        data={"slug": "demo", "name": "Demo", "version": "1.0.0", "summary": "d"},
        files={"file": ("demo.zip", _make_zip("v1"), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    skill = resp.json()["skill"]
    assert skill["slug"] == "demo"
    assert skill["latest_version"] == "1.0.0"

    # owner can download their own via data plane with gateway key
    from tests.test_backend_integration import _auth_headers

    dl = await client.get(
        f"/v1/registry/skills/{username}/demo/versions/latest/download",
        headers=_auth_headers(gw_key),
    )
    assert dl.status_code == 200
    assert dl.content[:2] == b"PK"

    # append a new version
    resp2 = await client.post(
        "/auth/registry/skills",
        headers=sess_headers,
        data={"slug": "demo", "name": "Demo", "version": "2.0.0"},
        files={"file": ("demo.zip", _make_zip("v2"), "application/zip")},
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["skill"]["latest_version"] == "2.0.0"


async def test_self_service_duplicate_version_409(client):
    sess_headers, *_ = await _login_user_with_key(client)
    payload = {
        "slug": "dup",
        "name": "Dup",
        "version": "1.0.0",
    }
    r1 = await client.post(
        "/auth/registry/skills", headers=sess_headers, data=payload,
        files={"file": ("d.zip", _make_zip(), "application/zip")},
    )
    assert r1.status_code == 200
    r2 = await client.post(
        "/auth/registry/skills", headers=sess_headers, data=payload,
        files={"file": ("d.zip", _make_zip(), "application/zip")},
    )
    assert r2.status_code == 409
    assert r2.json()["detail"] == "version_conflict"


async def test_self_service_ownership_enforced(client):
    """A different user cannot manage alice/demo."""
    alice_headers, _, alice_user, _ = await _login_user_with_key(client)
    bob_headers, *_ = await _login_user_with_key(client)

    # alice creates demo
    await client.post(
        "/auth/registry/skills", headers=alice_headers,
        data={"slug": "owned", "name": "Owned", "version": "1.0.0"},
        files={"file": ("o.zip", _make_zip(), "application/zip")},
    )
    # bob tries to upload the same slug -> conflict (different owner)
    r = await client.post(
        "/auth/registry/skills", headers=bob_headers,
        data={"slug": "owned", "name": "Owned", "version": "1.0.0"},
        files={"file": ("o.zip", _make_zip(), "application/zip")},
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "artifact_slug_conflict"


async def test_self_service_grants_lifecycle(client):
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.db.models import Team
    from sqlmodel import select as sqlselect
    from sqlmodel import col as _col

    sess_headers, *_ = await _login_user_with_key(client)
    await client.post(
        "/auth/registry/skills", headers=sess_headers,
        data={"slug": "g", "name": "G", "version": "1.0.0"},
        files={"file": ("g.zip", _make_zip(), "application/zip")},
    )
    async with AsyncSessionLocal() as session:
        guest = (
            await session.execute(sqlselect(Team).where(_col(Team.name) == "guest"))
        ).scalar_one()
        guest_id = str(guest.id)

    # grant to guest
    r = await client.post(
        "/auth/registry/skills/me/g/grants", headers=sess_headers,
        json={"team_id": guest_id},
    )
    assert r.status_code == 200, r.text
    grant_id = r.json()["grant"]["id"]

    # list grants
    g = await client.get("/auth/registry/skills/me/g/grants", headers=sess_headers)
    assert g.status_code == 200
    assert any(gr["id"] == grant_id for gr in g.json()["items"])

    # revoke
    rev = await client.patch(
        f"/auth/registry/skills/me/g/grants/{grant_id}/state",
        headers=sess_headers, json={"state": "disabled"},
    )
    assert rev.status_code == 200
    assert rev.json()["grant"]["state"] == "disabled"


async def test_self_service_upload_too_large_413(client):
    sess_headers, *_ = await _login_user_with_key(client)
    big = b"0" * (1 + 10 * 1024 * 1024)  # just over default 10MiB
    r = await client.post(
        "/auth/registry/skills", headers=sess_headers,
        data={"slug": "big", "name": "Big", "version": "1.0.0"},
        files={"file": ("big.zip", big, "application/zip")},
    )
    assert r.status_code == 413
```

> Note: several tests above reference the owner via the literal path segment `me` (e.g. `/auth/registry/skills/me/g/grants`). The self-service routes below treat `me` as an alias for the authenticated session subject, so the owner is implicit and cannot be spoofed by path.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_skills.py -q -k "self_service"`
Expected: FAIL — routes don't exist (404).

- [ ] **Step 3: Implement the self-service routes**

Append to `src/llm_gateway/api/auth.py`. The file already imports `APIRouter`, `Depends`, `HTTPException`, `status`, `BaseModel`, `session_dep`, `user_session_dep`, `settings_dep`, `UserSessionContext`, and the common models. Add these additional imports near the top of the file's import block (after the existing model imports):

```python
from uuid import UUID
from llm_gateway.db.models import (
    Subject as _Subject,  # alias only if `Subject` already imported under another name
)
```

If `Subject`, `Skill`, `SkillTeamGrant`, `Team` are not already imported in `auth.py`, add them to its existing `from llm_gateway.db.models import (...)` block.

Then append the routes:

```python
# ---- marketplace: self-service skill registry ----

from fastapi import UploadFile, File, Form
from llm_gateway.core.config import Settings as _Settings
from llm_gateway.db.models import Skill, SkillTeamGrant, Team
from llm_gateway.services.registry import (
    SLUG_PATTERN,
    create_or_append_skill_version,
    ensure_skill_team_grant,
    get_skill_by_owner_slug,
)
from llm_gateway.services.resource_payloads import skill_detail, skill_summary


class SkillGrantCreate(BaseModel):
    team_id: UUID


@router.post("/registry/skills")
async def upload_skill(
    slug: str = Form(...),
    name: str = Form(...),
    version: str = Form(...),
    summary: str | None = Form(default=None),
    description: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    file: UploadFile = File(...),
    ctx: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
    settings: _Settings = Depends(settings_dep),
):
    import re

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


@router.get("/registry/skills")
async def list_my_skills(
    ctx: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    from sqlalchemy import select as _select
    from sqlmodel import col as _col

    stmt = (
        _select(Skill)
        .where(
            _col(Skill.owner_subject_id) == ctx.subject.id,
        )
        .order_by(_col(Skill.updated_at).desc())
    )
    items = list((await session.execute(stmt)).scalars().all())
    return {
        "items": [skill_summary(s, owner_name=ctx.subject.name) for s in items],
        "total": len(items),
    }


@router.patch("/registry/skills/me/{slug}")
async def update_my_skill(
    slug: str,
    payload: dict,
    ctx: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await get_skill_by_owner_slug(
        session, owner_id=ctx.subject.id, slug=slug, include_disabled=True
    )
    if skill is None:
        raise HTTPException(status_code=404, detail="artifact_not_found")
    if skill.owner_subject_id != ctx.subject.id:
        raise HTTPException(status_code=403, detail="not_artifact_owner")
    for field_name in ("name", "summary", "description", "notes", "state"):
        if field_name in payload:
            setattr(skill, field_name, payload[field_name])
    await session.commit()
    await session.refresh(skill)
    return {"skill": skill_summary(skill, owner_name=ctx.subject.name)}


@router.get("/registry/skills/me/{slug}/grants")
async def list_my_skill_grants(
    slug: str,
    ctx: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await get_skill_by_owner_slug(
        session, owner_id=ctx.subject.id, slug=slug
    )
    if skill is None or skill.owner_subject_id != ctx.subject.id:
        raise HTTPException(status_code=404, detail="artifact_not_found")
    from sqlalchemy import select as _select
    from sqlmodel import col as _col

    rows = (
        await session.execute(
            _select(SkillTeamGrant).where(_col(SkillTeamGrant.skill_id) == skill.id)
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


@router.post("/registry/skills/me/{slug}/grants")
async def create_my_skill_grant(
    slug: str,
    payload: SkillGrantCreate,
    ctx: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    skill = await get_skill_by_owner_slug(
        session, owner_id=ctx.subject.id, slug=slug
    )
    if skill is None or skill.owner_subject_id != ctx.subject.id:
        raise HTTPException(status_code=404, detail="artifact_not_found")
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


@router.patch("/registry/skills/me/{slug}/grants/{grant_id}/state")
async def patch_my_skill_grant_state(
    slug: str,
    grant_id: UUID,
    payload: dict,
    ctx: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    from llm_gateway.db.models import ResourceState, utcnow

    skill = await get_skill_by_owner_slug(
        session, owner_id=ctx.subject.id, slug=slug
    )
    if skill is None or skill.owner_subject_id != ctx.subject.id:
        raise HTTPException(status_code=404, detail="artifact_not_found")
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
```

- [ ] **Step 4: Run self-service tests to verify they pass**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_skills.py -q -k "self_service"`
Expected: all PASS.

- [ ] **Step 5: Run the previously-deferred hidden-404 test**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_skills.py::test_dataplane_hidden_skill_returns_404 -q`

If the placeholder assertion logic in that early test is now incorrect (it asserted `in (200, 404)` as a placeholder), replace its body with the proper hidden-404 assertion now:

```python
async def test_dataplane_hidden_skill_returns_404(client):
    from tests.test_backend_integration import _auth_headers

    alice_sess, _, alice_user, _ = await _login_user_with_key(client)
    _, other_gw, _, _ = await _login_user_with_key(client)

    # alice uploads a private skill (no grant to anyone)
    up = await client.post(
        "/auth/registry/skills",
        headers=alice_sess,
        data={"slug": "private", "name": "Private", "version": "1.0.0"},
        files={"file": ("p.zip", _make_zip(), "application/zip")},
    )
    assert up.status_code == 200, up.text

    # other user (only a guest member, skill not granted) -> 404, existence hidden
    dl = await client.get(
        f"/v1/registry/skills/{alice_user}/private/versions/latest/download",
        headers=_auth_headers(other_gw),
    )
    assert dl.status_code == 404
    assert dl.json()["detail"] == "artifact_not_found"
```

Re-run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_skills.py::test_dataplane_hidden_skill_returns_404 -q`
Expected: PASS.

- [ ] **Step 6: Run the full marketplace test file**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_skills.py -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add src/llm_gateway/api/auth.py tests/test_marketplace_skills.py
git commit -m "Add self-service /auth/registry/skills routes (upload/grants/ownership)"
```

---

## Task 11: Super-admin router `/admin/registry/*`

**Files:**
- Create: `src/llm_gateway/api/admin/marketplace.py`
- Modify: `src/llm_gateway/api/admin/__init__.py` (register sub-router)
- Test: `tests/test_marketplace_skills.py` (append)

- [ ] **Step 1: Write failing test for admin cross-owner management**

Append to `tests/test_marketplace_skills.py`:

```python
async def _admin_headers(client):
    from llm_gateway.core.config import get_settings

    login = await client.post(
        "/auth/login",
        json={
            "username": get_settings().bootstrap_admin_username,
            "password": get_settings().bootstrap_admin_password,
        },
    )
    assert login.status_code == 200, login.text
    return {"x-session-token": login.json()["session_token"]}


async def test_admin_can_list_skill_team_grants(client):
    sess_headers, *_ = await _login_user_with_key(client)
    await client.post(
        "/auth/registry/skills", headers=sess_headers,
        data={"slug": "adm", "name": "Adm", "version": "1.0.0"},
        files={"file": ("a.zip", _make_zip(), "application/zip")},
    )
    admin = await _admin_headers(client)
    resp = await client.get("/admin/registry/skill-team-grants", headers=admin)
    assert resp.status_code == 200, resp.text
    assert "items" in resp.json()


async def test_admin_can_disable_any_skill(client):
    sess_headers, *_ = await _login_user_with_key(client)
    up = await client.post(
        "/auth/registry/skills", headers=sess_headers,
        data={"slug": "target", "name": "Target", "version": "1.0.0"},
        files={"file": ("t.zip", _make_zip(), "application/zip")},
    )
    skill_id = up.json()["skill"]["id"]
    admin = await _admin_headers(client)
    r = await client.patch(
        f"/admin/registry/skills/{skill_id}/state",
        headers=admin, json={"state": "disabled"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["skill"]["state"] == "disabled"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_skills.py -q -k "admin"`
Expected: FAIL — routes 404.

- [ ] **Step 3: Create the admin marketplace router**

Create `src/llm_gateway/api/admin/marketplace.py`:

```python
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.api.admin._common import StatePatch, _audit_update, _get_or_404
from llm_gateway.api.deps import session_dep
from llm_gateway.db.models import ResourceState, Skill, SkillTeamGrant, Team, utcnow
from llm_gateway.services.resource_payloads import skill_summary

router = APIRouter()


class SkillTeamGrantCreate(BaseModel):
    skill_id: UUID
    team_id: UUID


@router.get("/skill-team-grants")
async def list_skill_team_grants(session: AsyncSession = Depends(session_dep)):
    rows = (
        await session.execute(
            select(SkillTeamGrant).order_by(col(SkillTeamGrant.created_at).desc())
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
    await session.commit()
    await session.refresh(grant)
    await _audit_update(
        session,
        action="skill_team_grant.create",
        resource_type="skill_team_grant",
        resource_id=grant.id,
        payload=payload,
    )
    await session.commit()
    return {"grant": _grant_dict(grant)}


def _grant_dict(g: SkillTeamGrant) -> dict:
    return {
        "id": str(g.id),
        "skill_id": str(g.skill_id),
        "team_id": str(g.team_id),
        "state": g.state.value if hasattr(g.state, "value") else g.state,
    }


@router.get("/skills/{skill_id}")
async def admin_get_skill(
    skill_id: UUID, session: AsyncSession = Depends(session_dep)
):
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
    await session.commit()
    await session.refresh(skill)
    return {"skill": skill_summary(skill)}
```

- [ ] **Step 4: Register the sub-router**

In `src/llm_gateway/api/admin/__init__.py`, add the import and include:

```python
from llm_gateway.api.admin import (
    access, identity, marketplace, observability, policy, routing,
)
```
(extend the existing import), and after `router.include_router(policy.router)`:

```python
router.include_router(marketplace.router)
```

- [ ] **Step 5: Run admin tests to verify they pass**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_skills.py -q -k "admin"`
Expected: all PASS.

- [ ] **Step 6: Run the entire marketplace test suite + a broad regression smoke**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest tests/test_marketplace_skills.py -q && uv run pytest tests/test_self_key_management.py tests/test_access_revocation.py -q`
Expected: all PASS (marketplace + no regression in auth/access).

- [ ] **Step 7: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add src/llm_gateway/api/admin/marketplace.py src/llm_gateway/api/admin/__init__.py tests/test_marketplace_skills.py
git commit -m "Add super-admin /admin/registry marketplace router (grants CRUD, skill state)"
```

---

## Task 12: Frontend — types + client + nav section

**Files:**
- Modify: `frontend/src/lib/api/types.ts`
- Modify: `frontend/src/lib/api/client.ts`
- Modify: `frontend/src/lib/admin-config.ts`

- [ ] **Step 1: Add types**

Append to `frontend/src/lib/api/types.ts`:

```typescript
export interface SkillSummary {
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

export interface SkillVersionSummary {
	version: string;
	content_sha256: string;
	size_bytes: number;
	upload_subject_id: string;
	state: string;
	created_at: string | null;
}

export interface SkillTeamGrantSummary {
	id: string;
	skill_id: string;
	team_id: string;
	state: string;
}

export interface SkillDetail extends SkillSummary {
	description: string | null;
	notes: string | null;
	versions: SkillVersionSummary[];
	grants: SkillTeamGrantSummary[];
}

export interface Paginated<T> {
	items: T[];
	total: number;
	page?: number;
	size?: number;
}
```

- [ ] **Step 2: Add client methods**

In `frontend/src/lib/api/client.ts`, add these methods to the existing `AdminApiClient` class (the class already sends `x-session-token` / `x-admin-token` headers; self-service routes need the session token, which it carries):

```typescript
	async listMySkills(): Promise<Paginated<SkillSummary>> {
		return this.get('/auth/registry/skills');
	}

	async uploadSkill(
		form: { slug: string; name: string; version: string; summary?: string; description?: string; notes?: string },
		file: File
	): Promise<{ skill: SkillSummary }> {
		const fd = new FormData();
		fd.append('file', file);
		fd.append('slug', form.slug);
		fd.append('name', form.name);
		fd.append('version', form.version);
		if (form.summary) fd.append('summary', form.summary);
		if (form.description) fd.append('description', form.description);
		if (form.notes) fd.append('notes', form.notes);
		return this.post('/auth/registry/skills', fd);
	}

	async listSkillGrants(slug: string): Promise<Paginated<SkillTeamGrantSummary>> {
		return this.get(`/auth/registry/skills/me/${encodeURIComponent(slug)}/grants`);
	}

	async grantSkill(slug: string, teamId: string): Promise<{ grant: SkillTeamGrantSummary }> {
		return this.post(`/auth/registry/skills/me/${encodeURIComponent(slug)}/grants`, { team_id: teamId });
	}

	async revokeSkillGrant(slug: string, grantId: string): Promise<{ grant: SkillTeamGrantSummary }> {
		return this.patch(`/auth/registry/skills/me/${encodeURIComponent(slug)}/grants/${grantId}/state`, { state: 'disabled' });
	}
```

(Adjust method names like `this.get`/`this.post`/`this.patch` to match the actual helper method names already defined in `client.ts`. If the client uses a different convention — e.g. a single `request(method, path, body)` — translate accordingly. Inspect the existing `get`/`post` helpers in the file first.)

- [ ] **Step 3: Add nav section + slug validator**

In `frontend/src/lib/admin-config.ts`, import `Package` from `lucide-svelte` (add to the existing import block), and add to the `sections` array (after the `teams` entry):

```typescript
	{ id: 'skill-market', label: 'Skill 市场', group: '市场', icon: Package },
```

Also add a slug pattern export near the other helpers:

```typescript
export const marketSlugPattern = /^[a-z][a-z0-9-]*$/;
```

- [ ] **Step 4: Verify frontend type-checks**

Run: `cd /Users/liyifan/llm_gateway/frontend && npm run check`
Expected: no type errors related to the new additions.

- [ ] **Step 5: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add frontend/src/lib/api/types.ts frontend/src/lib/api/client.ts frontend/src/lib/admin-config.ts
git commit -m "Add Skill marketplace frontend types, client methods, nav section"
```

---

## Task 13: Frontend — Skill market UI components

**Files:**
- Create: `frontend/src/lib/components/UploadSkillDialog.svelte`
- Create: `frontend/src/lib/components/ArtifactGrantsEditor.svelte`
- Create: `frontend/src/lib/components/SkillMarketSection.svelte`
- Modify: `frontend/src/routes/+page.svelte`

- [ ] **Step 1: Create the upload dialog**

Create `frontend/src/lib/components/UploadSkillDialog.svelte`. A modal form collecting slug (validated against `marketSlugPattern`), name, version, summary, and a `.zip` file pick (size checked against 10 MiB client-side). On submit it calls `client.uploadSkill(...)`. Follow the styling/props conventions of an existing dialog component in `lib/components/` (e.g. inspect `SecretOnceDialog.svelte` for the modal wrapper pattern: a fixed overlay with a centered card and an emit `close` event).

```svelte
<script lang="ts">
	import { marketSlugPattern } from '$lib/admin-config';
	import type { AdminApiClient } from '$lib/api/client';
	import type { SkillSummary } from '$lib/api/types';
	import { createEventDispatcher } from 'svelte';

	let { client, existingSlugs = [] }: { client: AdminApiClient; existingSlugs?: string[] } = $props();
	let dispatch = createEventDispatcher();

	let slug = $state('');
	let name = $state('');
	let version = $state('');
	let summary = $state('');
	let file = $state<File | null>(null);
	let busy = $state(false);
	let error = $state<string | null>(null);

	const MAX_BYTES = 10 * 1024 * 1024;
	let slugValid = $derived(marketSlugPattern.test(slug));
	let canSubmit = $derived(slugValid && name.length > 0 && version.length > 0 && !!file && !busy);

	async function onPick(ev: Event) {
		const target = ev.target as HTMLInputElement;
		file = target.files?.[0] ?? null;
		if (file && file.size > MAX_BYTES) {
			error = '文件超过 10 MiB 上限';
			file = null;
		}
	}

	async function submit() {
		if (!file) return;
		busy = true;
		error = null;
		try {
			await client.uploadSkill(
				{ slug, name, version, summary: summary || undefined },
				file
			);
			dispatch('uploaded');
			dispatch('close');
		} catch (e) {
			error = String(e);
		} finally {
			busy = false;
		}
	}
</script>

<div class="overlay">
	<div class="card">
		<h3>上传 Skill</h3>
		<label>slug<input value={slug} oninput={(e) => (slug = e.currentTarget.value)} /></label>
		{#if slug && !slugValid}<small class="err">小写字母开头，仅小写字母/数字/连字符</small>{/if}
		<label>name<input value={name} oninput={(e) => (name = e.currentTarget.value)} /></label>
		<label>version<input value={version} oninput={(e) => (version = e.currentTarget.value)} /></label>
		<label>summary<input value={summary} oninput={(e) => (summary = e.currentTarget.value)} /></label>
		<label>zip 文件<input type="file" accept=".zip,application/zip" onchange={onPick} /></label>
		{#if error}<small class="err">{error}</small>{/if}
		<div class="actions">
			<button onclick={() => dispatch('close')} disabled={busy}>取消</button>
			<button onclick={submit} disabled={!canSubmit}>{busy ? '上传中…' : '上传'}</button>
		</div>
	</div>
</div>

<style>
	.overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; }
	.card { background: #fff; padding: 1.5rem; border-radius: 8px; min-width: 360px; display: grid; gap: 0.5rem; }
	label { display: grid; gap: 0.25rem; font-size: 0.85rem; }
	.err { color: #c00; }
	.actions { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.5rem; }
</style>
```

- [ ] **Step 2: Create the grants editor (shared skill/mcp)**

Create `frontend/src/lib/components/ArtifactGrantsEditor.svelte`. Given a skill slug and the list of all teams, render existing grants and an "add team" selector. On revoke it calls `client.revokeSkillGrant`.

```svelte
<script lang="ts">
	import type { AdminApiClient } from '$lib/api/client';
	import type { SkillTeamGrantSummary } from '$lib/api/types';

	let {
		client,
		slug,
		grants,
		teams
	}: {
		client: AdminApiClient;
		slug: string;
		grants: SkillTeamGrantSummary[];
		teams: { id: string; name: string }[];
	} = $props();

	let selectedTeam = $state('');
	let busy = $state(false);

	async function add() {
		if (!selectedTeam) return;
		busy = true;
		try {
			await client.grantSkill(slug, selectedTeam);
			selectedTeam = '';
			window.dispatchEvent(new CustomEvent('marketplace-refresh'));
		} finally {
			busy = false;
		}
	}

	async function revoke(id: string) {
		busy = true;
		try {
			await client.revokeSkillGrant(slug, id);
			window.dispatchEvent(new CustomEvent('marketplace-refresh'));
		} finally {
			busy = false;
		}
	}

	function teamName(id: string) {
		return teams.find((t) => t.id === id)?.name ?? id.slice(0, 8);
	}
</script>

<div class="grants">
	<ul>
		{#each grants as g (g.id)}
			<li>
				<span>{teamName(g.team_id)}</span>
				<small>{g.state}</small>
				{#if g.state === 'active'}<button onclick={() => revoke(g.id)} disabled={busy}>撤销</button>{/if}
			</li>
		{/each}
	</ul>
	<div class="add">
		<select bind:value={selectedTeam}>
			<option value="">选择权限组…</option>
			{#each teams as t (t.id)}<option value={t.id}>{t.name}</option>{/each}
		</select>
		<button onclick={add} disabled={!selectedTeam || busy}>授权</button>
	</div>
</div>

<style>
	.grants { display: grid; gap: 0.5rem; }
	ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 0.25rem; }
	li { display: flex; align-items: center; gap: 0.5rem; }
	.add { display: flex; gap: 0.5rem; }
</style>
```

- [ ] **Step 3: Create the market section**

Create `frontend/src/lib/components/SkillMarketSection.svelte`. Lists "my skills" in a table (reuse `ResourceTable` if its props accept generic columns; otherwise a plain table). "Upload" opens the dialog; clicking a row expands versions + the grants editor.

```svelte
<script lang="ts">
	import type { AdminApiClient } from '$lib/api/client';
	import type { SkillSummary, SkillDetail } from '$lib/api/types';
	import UploadSkillDialog from './UploadSkillDialog.svelte';
	import ArtifactGrantsEditor from './ArtifactGrantsEditor.svelte';

	let { client, teams }: { client: AdminApiClient; teams: { id: string; name: string }[] } = $props();

	let skills = $state<SkillSummary[]>([]);
	let showUpload = $state(false);
	let selected = $state<SkillDetail | null>(null);
	let loading = $state(false);

	async function load() {
		loading = true;
		try {
			const page = await client.listMySkills();
			skills = page.items;
		} finally {
			loading = false;
		}
	}

	async function open(skill: SkillSummary) {
		// detail endpoint is on the data plane; the console is session-authed, so
		// fall back to a self-service detail fetch if needed. For Slice 1 the
		// summary row + grants editor is sufficient; full detail drawer can use
		// the data-plane GET with an operator gateway key if available.
		selected = {
			...skill,
			description: null,
			notes: null,
			versions: [],
			grants: []
		} as SkillDetail;
		const grants = await client.listSkillGrants(skill.slug);
		if (selected) selected.grants = grants.items;
	}

	$effect(() => {
		void load();
	});

	function onRefresh() {
		void load();
		if (selected) {
			void client.listSkillGrants(selected.slug).then((g) => {
				if (selected) selected.grants = g.items;
			});
		}
	}

	window.addEventListener('marketplace-refresh', onRefresh);
</script>

<div class="section">
	<header>
		<h2>Skill 市场 — 我的制品</h2>
		<button onclick={() => (showUpload = true)}>+ 上传 Skill</button>
	</header>

	{#if loading}<p>加载中…</p>{/if}

	<table>
		<thead><tr><th>slug</th><th>name</th><th>latest</th><th>state</th><th>授权组</th></tr></thead>
		<tbody>
			{#each skills as s (s.id)}
				<tr onclick={() => open(s)} class={selected?.id === s.id ? 'active' : ''}>
					<td>{s.slug}</td><td>{s.name}</td>
					<td>{s.latest_version ?? '-'}</td><td>{s.state}</td>
					<td>{selected?.id === s.id ? selected.grants.length : ''}</td>
				</tr>
			{/each}
		</tbody>
	</table>

	{#if selected}
		<div class="detail">
			<h3>{selected.slug}</h3>
			<ArtifactGrantsEditor {client} slug={selected.slug} grants={selected.grants} {teams} />
		</div>
	{/if}

	{#if showUpload}
		<UploadSkillDialog {client} onuploaded={onRefresh} onclose={() => (showUpload = false)} />
	{/if}
</div>

<style>
	.section { display: grid; gap: 1rem; }
	header { display: flex; justify-content: space-between; align-items: center; }
	table { width: 100%; border-collapse: collapse; }
	tr { cursor: pointer; }
	tr.active { background: #eef; }
	td, th { padding: 0.4rem; border-bottom: 1px solid #eee; text-align: left; }
</style>
```

> The Svelte 5 `$effect` + `window.addEventListener` pattern above is illustrative. Match whatever refresh pattern the other sections in `+page.svelte` use (e.g. a shared reactive `refreshSignal` store). Inspect how an existing section like the `teams` view reloads after a mutation and follow that exactly; if the codebase dispatches refresh differently, adapt `onRefresh` accordingly.

- [ ] **Step 4: Wire the section into `+page.svelte`**

In `frontend/src/routes/+page.svelte`, find the view-switch block (the code that renders the section matching the active `section.id`). Add a branch for `'skill-market'` that renders `<SkillMarketSection {client} teams={inventory.teams} />`, importing the component at the top. Pass the already-available `client` instance and the `teams` array from the existing inventory/profile state.

- [ ] **Step 5: Type-check and run unit tests**

Run: `cd /Users/lififan/llm_gateway/frontend && npm run check && npm run test`
Expected: type-check clean; existing unit tests still pass.

- [ ] **Step 6: Manual smoke (optional but recommended)**

Start the stack (`uv run python scripts/start_local.py`), log into the console, open the "市场 → Skill 市场" section, upload a small zip, grant it to the `guest` team, then from a terminal run:
```bash
curl -s "http://127.0.0.1:18080/v1/registry/skills?q=<your-slug>" -H "Authorization: Bearer <your-gw-key>"
curl -s -o /tmp/s.zip "http://127.0.0.1:18080/v1/registry/skills/<owner>/<slug>/versions/latest/download" -H "Authorization: Bearer <your-gw-key>" && unzip -l /tmp/s.zip
```
Expected: the skill appears in the list and downloads as a valid zip.

- [ ] **Step 7: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add frontend/src/lib/components/UploadSkillDialog.svelte \
        frontend/src/lib/components/ArtifactGrantsEditor.svelte \
        frontend/src/lib/components/SkillMarketSection.svelte \
        frontend/src/routes/+page.svelte
git commit -m "Add Skill market UI: upload dialog, grants editor, market section"
```

---

## Task 14: Final verification + docs

**Files:**
- Modify: `README.md` (append a short marketplace section)

- [ ] **Step 1: Run the complete test suite**

Run: `cd /Users/liyifan/llm_gateway && uv run pytest -q`
Expected: all PASS (marketplace + full regression).

- [ ] **Step 2: Run frontend checks**

Run: `cd /Users/liyifan/llm_gateway/frontend && npm run check && npm run test && npm run test:e2e`
Expected: all pass. If e2e (`smoke.spec.ts`) doesn't cover the new section, that's acceptable for Slice 1 — note it as a follow-up.

- [ ] **Step 3: Document the marketplace in README**

Append a section to `README.md`:

```markdown
## Marketplace (Skill)

The gateway hosts a Skill marketplace. Any logged-in user can upload a skill
(`POST /auth/registry/skills`, multipart: metadata + zip) and grant access to
permission groups (`/auth/registry/skills/me/{slug}/grants`). Downstream agents
browse and download with their gateway key:

    GET  /v1/registry/skills                       # list visible skills
    GET  /v1/registry/skills/{owner}/{slug}        # detail + versions
    GET  /v1/registry/skills/{owner}/{slug}/versions/latest/download

Access = team grant. Granting the builtin `guest` team makes a skill public
(every user is a guest member). No grant = owner-only (private).

Admins manage any artifact under `/admin/registry/*`.
```

- [ ] **Step 4: Commit**

```bash
cd /Users/liyifan/llm_gateway
git add README.md
git commit -m "Document Skill marketplace in README"
```

---

## Notes for the implementer

- **`me` alias:** self-service routes under `/auth/registry/skills/me/{slug}/*` use `me` as the owner — the owner is always the session subject, resolved server-side from `ctx.subject.id`. Never accept a real username in those paths; that would let one user act on another's artifact.
- **Existence hiding:** data-plane lookups return 404 `artifact_not_found` for both "doesn't exist" and "exists but not visible to you" — never 403, never reveal the slug is taken.
- **Size enforcement:** the server re-checks `len(zip_bytes) <= settings.marketplace_skill_max_bytes` on every upload. Do not rely on the client check.
- **Audit:** `create_or_append_skill_version` writes a `skill.create` / `skill.upload_version` audit event. Admin grant writes go through `_audit_update`. The `env`/`headers` keys are already in `_AUDIT_SENSITIVE_KEYS`.
- **Slice 2 readiness:** all 6 tables and 6 entities are created in Tasks 1–2. MCP service functions / routes / UI are NOT in this plan — they are Slice 2.
