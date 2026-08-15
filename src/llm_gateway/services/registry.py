from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import distinct, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.db.models import (
    MCP,
    MCPTransport,
    McpLike,
    McpTeamGrant,
    McpVersion,
    ResourceState,
    Skill,
    SkillLike,
    SkillTeamGrant,
    SkillVersion,
    Subject,
    Team,
    TeamMembership,
    utcnow,
)
from llm_gateway.services.facts import record_audit_event
from llm_gateway.services.resource_payloads import mcp_detail, skill_detail


SLUG_PATTERN = r"^[a-z][a-z0-9-]*$"


# ---------------------------------------------------------------------------
# Artifact spec: skills and MCPs implement the exact same marketplace pipeline
# (visible-listing, likes, versions, team grants, upload) against different
# tables. Every generic helper below is parameterized by one of these bundles;
# the historical public functions stay as thin wrappers so no caller changes.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactSpec:
    entity: Any    # Skill | MCP
    grant: Any     # SkillTeamGrant | McpTeamGrant
    like: Any      # SkillLike | McpLike
    version: Any   # SkillVersion | McpVersion
    fk: str        # name of the entity FK column on grant/like/version rows
    kind: str      # "skill" | "mcp" — audit action prefix + resource_type

    @property
    def grant_entity_fk(self) -> Any:
        return getattr(self.grant, self.fk)

    @property
    def like_entity_fk(self) -> Any:
        return getattr(self.like, self.fk)

    @property
    def version_entity_fk(self) -> Any:
        return getattr(self.version, self.fk)


_SKILLS = ArtifactSpec(
    entity=Skill,
    grant=SkillTeamGrant,
    like=SkillLike,
    version=SkillVersion,
    fk="skill_id",
    kind="skill",
)
_MCPS = ArtifactSpec(
    entity=MCP,
    grant=McpTeamGrant,
    like=McpLike,
    version=McpVersion,
    fk="mcp_id",
    kind="mcp",
)


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


async def resolve_owner_names(
    session: AsyncSession, *, owner_ids: Iterable[UUID]
) -> dict[Any, str]:
    """Map owner_subject_id -> Subject.name for the given ids (shared by every
    marketplace list/detail surface)."""
    ids = set(owner_ids)
    if not ids:
        return {}
    rows = await session.execute(
        select(Subject.id, Subject.name).where(col(Subject.id).in_(ids))
    )
    return {row[0]: row[1] for row in rows.all()}


# ---------------------------------------------------------------------------
# Access control / lookup
# ---------------------------------------------------------------------------


async def _subject_can_access_artifact(
    session: AsyncSession, *, spec: ArtifactSpec, subject_id: UUID, artifact: Any
) -> bool:
    if artifact.owner_subject_id == subject_id:
        return True
    result = await session.execute(
        select(col(spec.grant.id))
        .join(Team, col(Team.id) == col(spec.grant.team_id))
        .join(TeamMembership, col(TeamMembership.team_id) == col(Team.id))
        .where(
            col(spec.grant_entity_fk) == artifact.id,
            col(spec.grant.state) == ResourceState.ACTIVE,
            col(Team.state) == ResourceState.ACTIVE,
            col(TeamMembership.state) == ResourceState.ACTIVE,
            col(TeamMembership.subject_id) == subject_id,
        )
    )
    return result.scalars().first() is not None


async def subject_can_access_skill(
    session: AsyncSession, *, subject_id: UUID, skill: Skill
) -> bool:
    """A subject may see a skill iff it is the owner OR a team it belongs to has
    an active grant for the skill. Mirrors the team-grant branch of
    services/policy.py:subject_can_use_model."""
    return await _subject_can_access_artifact(
        session, spec=_SKILLS, subject_id=subject_id, artifact=skill
    )


async def subject_can_access_mcp(
    session: AsyncSession, *, subject_id: UUID, mcp: MCP
) -> bool:
    """Same visibility rule as skills: owner OR active team grant."""
    return await _subject_can_access_artifact(
        session, spec=_MCPS, subject_id=subject_id, artifact=mcp
    )


async def _get_artifact_by_owner_slug(
    session: AsyncSession,
    *,
    spec: ArtifactSpec,
    owner_id: UUID,
    slug: str,
    include_disabled: bool = False,
) -> Any:
    stmt = select(spec.entity).where(
        col(spec.entity.owner_subject_id) == owner_id,
        col(spec.entity.slug) == slug,
    )
    if not include_disabled:
        stmt = stmt.where(col(spec.entity.state) == ResourceState.ACTIVE)
    return (await session.execute(stmt)).scalars().first()


async def get_skill_by_owner_slug(
    session: AsyncSession, *, owner_id: UUID, slug: str, include_disabled: bool = False
) -> Skill | None:
    return await _get_artifact_by_owner_slug(
        session,
        spec=_SKILLS,
        owner_id=owner_id,
        slug=slug,
        include_disabled=include_disabled,
    )


async def get_mcp_by_owner_slug(
    session: AsyncSession, *, owner_id: UUID, slug: str, include_disabled: bool = False
) -> MCP | None:
    return await _get_artifact_by_owner_slug(
        session,
        spec=_MCPS,
        owner_id=owner_id,
        slug=slug,
        include_disabled=include_disabled,
    )


async def _get_visible_artifact_or_404(
    session: AsyncSession,
    *,
    spec: ArtifactSpec,
    owner_name: str,
    slug: str,
    subject_id: UUID,
) -> Any:
    owner = await resolve_owner_subject(session, owner=owner_name)
    if owner is None:
        raise HTTPException(status_code=404, detail="artifact_not_found")
    stmt = select(spec.entity).where(
        col(spec.entity.owner_subject_id) == owner.id,
        col(spec.entity.slug) == slug,
    )
    artifact = (await session.execute(stmt)).scalars().first()
    if artifact is None or artifact.state != ResourceState.ACTIVE:
        raise HTTPException(status_code=404, detail="artifact_not_found")
    if not await _subject_can_access_artifact(
        session, spec=spec, subject_id=subject_id, artifact=artifact
    ):
        raise HTTPException(status_code=404, detail="artifact_not_found")
    return artifact


async def get_visible_skill_or_404(
    session: AsyncSession, *, owner_name: str, slug: str, subject_id: UUID
) -> Skill:
    """Fetch the skill named (owner_name, slug) that is ACTIVE and visible to
    subject_id (owner or active team grant); 404s for anything else."""
    return await _get_visible_artifact_or_404(
        session, spec=_SKILLS, owner_name=owner_name, slug=slug, subject_id=subject_id
    )


async def get_visible_mcp_or_404(
    session: AsyncSession, *, owner_name: str, slug: str, subject_id: UUID
) -> MCP:
    """Fetch the MCP named (owner_name, slug) that is ACTIVE and visible to
    subject_id (owner or active team grant); 404s for anything else."""
    return await _get_visible_artifact_or_404(
        session, spec=_MCPS, owner_name=owner_name, slug=slug, subject_id=subject_id
    )


# ---------------------------------------------------------------------------
# Upload pipeline
# ---------------------------------------------------------------------------


def _extract_readme(zip_bytes: bytes) -> str | None:
    """Extract a README (SKILL.md preferred, else README.md) from a skill zip.

    Looks at the zip root or one level deep (e.g. ``my-skill/SKILL.md``),
    case-insensitive on the basename. Rejects any path containing ``..``
    (path traversal guard). Caps content at 64KB. Returns None if the zip is
    unreadable or no markdown file is found — never raises, so README extraction
    failures never block an upload.
    """
    readme_max_bytes = 65536
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            # First pass: collect candidate (depth, name) entries.
            candidates: list[tuple[int, int, str]] = []
            for info in zf.infolist():
                if info.is_dir():
                    continue
                path = info.filename
                # Normalize separators and reject traversal segments.
                if ".." in path.replace("\\", "/").split("/"):
                    continue
                parts = [p for p in path.replace("\\", "/").split("/") if p]
                depth = len(parts)
                if depth > 2:
                    continue
                base = parts[-1].lower()
                if base == "skill.md":
                    # SKILL.md always wins; track its priority index 0.
                    candidates.append((0, depth, path))
                elif base == "readme.md":
                    candidates.append((1, depth, path))
            if not candidates:
                return None
            # Prefer SKILL.md (priority 0), then shallowest path, then name order.
            candidates.sort(key=lambda c: (c[0], c[1], c[2]))
            chosen = candidates[0][2]
            raw = zf.read(chosen)
            if len(raw) > readme_max_bytes:
                raw = raw[:readme_max_bytes]
            return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


async def _create_or_append_version(
    session: AsyncSession,
    *,
    spec: ArtifactSpec,
    actor: Subject,
    slug: str,
    version: str,
    name: str,
    summary: str | None,
    description: str | None,
    notes: str | None,
    readme: str | None,
    make_version_row: Callable[[UUID], Any],
    audit_detail: dict,
) -> Any:
    """Create an artifact (first version) or append a new version.

    Namespacing is (owner, slug): a different owner may use the same slug
    (alice/weather and bob/weather coexist), enforced by the composite
    UNIQUE(owner_subject_id, slug) constraint. This function only resolves
    the actor's own (actor.id, slug) row.
    If (actor, slug) does not exist -> create the artifact + first version.
    If it exists (actor is the owner) -> append a new version, make it latest.
    Duplicate version string on the same artifact -> 409 version_conflict.
    """
    existing = await _get_artifact_by_owner_slug(
        session, spec=spec, owner_id=actor.id, slug=slug, include_disabled=True
    )

    if existing is None:
        artifact = spec.entity(
            owner_subject_id=actor.id,
            slug=slug,
            name=name,
            summary=summary,
            description=description,
            notes=notes,
            latest_version=version,
            readme=readme,
        )
        session.add(artifact)
        await session.flush()
        session.add(make_version_row(artifact.id))
        await session.flush()
        action = f"{spec.kind}.create"
    else:
        dup = await session.execute(
            select(col(spec.version.id)).where(
                col(spec.version_entity_fk) == existing.id,
                col(spec.version.version) == version,
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
        if readme is not None:
            existing.readme = readme
        existing.latest_version = version
        existing.updated_at = utcnow()
        session.add(make_version_row(existing.id))
        await session.flush()
        artifact = existing
        action = f"{spec.kind}.upload_version"

    await record_audit_event(
        session,
        action=action,
        resource_type=spec.kind,
        resource_id=artifact.id,
        outcome="success",
        actor_subject_id=actor.id,
        detail=audit_detail,
    )
    return artifact


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

    The zip content is stored on the SkillVersion row; the README shown in the
    marketplace is extracted from the zip (SKILL.md preferred, else README.md).
    Duplicate version string on the same skill -> 409 version_conflict."""
    sha = hashlib.sha256(zip_bytes).hexdigest()
    readme_text = _extract_readme(zip_bytes)

    def make_version_row(skill_id: UUID) -> SkillVersion:
        return SkillVersion(
            skill_id=skill_id,
            version=version,
            content_blob=zip_bytes,
            content_sha256=sha,
            size_bytes=len(zip_bytes),
            upload_subject_id=actor.id,
        )

    return await _create_or_append_version(
        session,
        spec=_SKILLS,
        actor=actor,
        slug=slug,
        version=version,
        name=name,
        summary=summary,
        description=description,
        notes=notes,
        readme=readme_text,
        make_version_row=make_version_row,
        audit_detail={"slug": slug, "version": version, "sha256": sha[:16]},
    )


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
    readme: str | None = None,
) -> MCP:
    """Create an MCP config (first version) or append a new version.
    Namespacing is (owner, slug); a different owner may reuse the same slug.
    Duplicate version string on the same mcp -> 409 version_conflict."""
    cfg = _validate_mcp_config(config)

    def make_version_row(mcp_id: UUID) -> McpVersion:
        return McpVersion(
            mcp_id=mcp_id,
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

    return await _create_or_append_version(
        session,
        spec=_MCPS,
        actor=actor,
        slug=slug,
        version=version,
        name=name,
        summary=summary,
        description=description,
        notes=notes,
        readme=readme,
        make_version_row=make_version_row,
        audit_detail={
            "slug": slug,
            "version": version,
            "transport": cfg["transport"].value,
        },
    )


# ---------------------------------------------------------------------------
# Team grants
# ---------------------------------------------------------------------------


async def _ensure_team_grant(
    session: AsyncSession, *, spec: ArtifactSpec, entity_id: UUID, team_id: UUID
) -> Any:
    """Idempotent grant upsert: reactivate if exists, else create.
    Mirrors services/security.py:ensure_model_team_grant."""
    result = await session.execute(
        select(spec.grant).where(
            col(spec.grant_entity_fk) == entity_id,
            col(spec.grant.team_id) == team_id,
        )
    )
    grant = result.scalar_one_or_none()
    if grant:
        if grant.state != ResourceState.ACTIVE:
            grant.state = ResourceState.ACTIVE
            grant.updated_at = utcnow()
        return grant
    grant = spec.grant(**{spec.fk: entity_id, "team_id": team_id})
    session.add(grant)
    await session.flush()
    return grant


async def ensure_skill_team_grant(
    session: AsyncSession, *, skill_id: UUID, team_id: UUID
) -> SkillTeamGrant:
    """Idempotent grant upsert: reactivate if exists, else create.
    Mirrors services/security.py:ensure_model_team_grant."""
    return await _ensure_team_grant(
        session, spec=_SKILLS, entity_id=skill_id, team_id=team_id
    )


async def ensure_mcp_team_grant(
    session: AsyncSession, *, mcp_id: UUID, team_id: UUID
) -> McpTeamGrant:
    """Idempotent grant upsert: reactivate if exists, else create."""
    return await _ensure_team_grant(
        session, spec=_MCPS, entity_id=mcp_id, team_id=team_id
    )


# ---------------------------------------------------------------------------
# Visible listing (owner OR active team grant)
# ---------------------------------------------------------------------------


async def _list_visible_artifacts(
    session: AsyncSession,
    *,
    spec: ArtifactSpec,
    subject_id: UUID,
    q: str | None = None,
    owner: str | None = None,
    limit: int = 30,
    offset: int = 0,
    sort: str = "downloads",
) -> tuple[list[Any], int]:
    """Return artifacts visible to subject_id (owner of OR team-granted), with
    search.

    `sort` selects the primary ordering: ``"downloads"`` (default) orders by
    download_count desc then updated_at desc; ``"likes"`` orders by like_count
    desc then updated_at desc."""
    entity = spec.entity
    grant = spec.grant
    base_filter = [
        col(entity.state) == ResourceState.ACTIVE,
        or_(
            col(entity.owner_subject_id) == subject_id,
            col(entity.id).in_(
                select(distinct(col(spec.grant_entity_fk)))
                .join(Team, col(Team.id) == col(grant.team_id))
                .join(
                    TeamMembership,
                    col(TeamMembership.team_id) == col(Team.id),
                )
                .where(
                    col(grant.state) == ResourceState.ACTIVE,
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
                col(entity.name).ilike(needle),
                col(entity.summary).ilike(needle),
                col(entity.slug).ilike(needle),
            )
        )
    if owner:
        base_filter.append(
            col(entity.owner_subject_id).in_(
                select(col(Subject.id)).where(
                    or_(
                        col(Subject.login_username) == owner,
                        col(Subject.name) == owner,
                    )
                )
            )
        )
    count_stmt = select(func.count(distinct(col(entity.id)))).where(*base_filter)
    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    if sort == "likes":
        order = (col(entity.like_count).desc(), col(entity.updated_at).desc())
    else:
        order = (col(entity.download_count).desc(), col(entity.updated_at).desc())
    list_stmt = (
        select(entity)
        .where(*base_filter)
        .order_by(*order)
        .limit(limit)
        .offset(offset)
    )
    items = list((await session.execute(list_stmt)).scalars().all())
    return items, total


async def list_visible_skills(
    session: AsyncSession,
    *,
    subject_id: UUID,
    q: str | None = None,
    owner: str | None = None,
    limit: int = 30,
    offset: int = 0,
    sort: str = "downloads",
) -> tuple[list[Skill], int]:
    """Return skills visible to subject_id (owner of OR team-granted), with search.

    `sort` selects the primary ordering: ``"downloads"`` (default) orders by
    download_count desc then updated_at desc; ``"likes"`` orders by like_count
    desc then updated_at desc."""
    return await _list_visible_artifacts(
        session,
        spec=_SKILLS,
        subject_id=subject_id,
        q=q,
        owner=owner,
        limit=limit,
        offset=offset,
        sort=sort,
    )


async def list_visible_mcps(
    session: AsyncSession,
    *,
    subject_id: UUID,
    q: str | None = None,
    owner: str | None = None,
    limit: int = 30,
    offset: int = 0,
    sort: str = "downloads",
) -> tuple[list[MCP], int]:
    """Return mcps visible to subject_id (owner of OR team-granted), with search.

    `sort` selects the primary ordering: ``"downloads"`` (default) orders by
    download_count desc then updated_at desc; ``"likes"`` orders by like_count
    desc then updated_at desc."""
    return await _list_visible_artifacts(
        session,
        spec=_MCPS,
        subject_id=subject_id,
        q=q,
        owner=owner,
        limit=limit,
        offset=offset,
        sort=sort,
    )


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


async def _get_active_version_by_number(
    session: AsyncSession, *, spec: ArtifactSpec, entity_id: UUID, version: str
) -> Any:
    stmt = select(spec.version).where(
        col(spec.version_entity_fk) == entity_id,
        col(spec.version.version) == version,
        col(spec.version.state) == ResourceState.ACTIVE,
    )
    return (await session.execute(stmt)).scalars().first()


async def get_skill_version(
    session: AsyncSession, *, skill_id: UUID, version: str
) -> SkillVersion | None:
    return await _get_active_version_by_number(
        session, spec=_SKILLS, entity_id=skill_id, version=version
    )


async def get_mcp_version_row(
    session: AsyncSession, *, mcp_id: UUID, version: str
) -> McpVersion | None:
    return await _get_active_version_by_number(
        session, spec=_MCPS, entity_id=mcp_id, version=version
    )


async def _get_latest_active_version(
    session: AsyncSession, *, spec: ArtifactSpec, artifact: Any
) -> Any:
    """Resolve the latest_version pointer; if it points at a disabled row or is
    null, fall back to the most recent active version by created_at."""
    if artifact.latest_version:
        pointed = await _get_active_version_by_number(
            session, spec=spec, entity_id=artifact.id, version=artifact.latest_version
        )
        if pointed:
            return pointed
    stmt = (
        select(spec.version)
        .where(
            col(spec.version_entity_fk) == artifact.id,
            col(spec.version.state) == ResourceState.ACTIVE,
        )
        .order_by(col(spec.version.created_at).desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def get_latest_active_version(
    session: AsyncSession, *, skill: Skill
) -> SkillVersion | None:
    """Resolve the latest_version pointer; if it points at a disabled row or is
    null, fall back to the most recent active version by created_at."""
    return await _get_latest_active_version(session, spec=_SKILLS, artifact=skill)


async def get_latest_active_mcp_version(
    session: AsyncSession, *, mcp: MCP
) -> McpVersion | None:
    """Resolve the latest_version pointer; fall back to most recent active by created_at."""
    return await _get_latest_active_version(session, spec=_MCPS, artifact=mcp)


# ---------------------------------------------------------------------------
# Likes
# ---------------------------------------------------------------------------


async def _toggle_artifact_like(
    session: AsyncSession, *, spec: ArtifactSpec, subject_id: UUID, entity_id: UUID
) -> Any:
    """Idempotent like toggle: if not liked, create a like row + like_count += 1;
    if liked, delete it + like_count -= 1. Returns the updated artifact."""
    artifact = await session.get(spec.entity, entity_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact_not_found")
    existing = (
        await session.execute(
            select(spec.like).where(
                col(spec.like.subject_id) == subject_id,
                col(spec.like_entity_fk) == entity_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        artifact.like_count = max(0, (artifact.like_count or 0) - 1)
    else:
        session.add(spec.like(subject_id=subject_id, **{spec.fk: entity_id}))
        artifact.like_count = (artifact.like_count or 0) + 1
    artifact.updated_at = utcnow()
    await session.flush()
    return artifact


async def toggle_skill_like(
    session: AsyncSession, *, subject_id: UUID, skill_id: UUID
) -> Skill:
    """Idempotent like toggle: if not liked, create SkillLike + like_count += 1;
    if liked, delete it + like_count -= 1. Returns the updated skill."""
    return await _toggle_artifact_like(
        session, spec=_SKILLS, subject_id=subject_id, entity_id=skill_id
    )


async def toggle_mcp_like(
    session: AsyncSession, *, subject_id: UUID, mcp_id: UUID
) -> MCP:
    """Idempotent like toggle: if not liked, create McpLike + like_count += 1;
    if liked, delete it + like_count -= 1. Returns the updated mcp."""
    return await _toggle_artifact_like(
        session, spec=_MCPS, subject_id=subject_id, entity_id=mcp_id
    )


async def _is_artifact_liked_by(
    session: AsyncSession, *, spec: ArtifactSpec, subject_id: UUID, entity_id: UUID
) -> bool:
    row = (
        await session.execute(
            select(col(spec.like.id)).where(
                col(spec.like.subject_id) == subject_id,
                col(spec.like_entity_fk) == entity_id,
            )
        )
    ).scalars().first()
    return row is not None


async def is_skill_liked_by(
    session: AsyncSession, *, subject_id: UUID, skill_id: UUID
) -> bool:
    return await _is_artifact_liked_by(
        session, spec=_SKILLS, subject_id=subject_id, entity_id=skill_id
    )


async def is_mcp_liked_by(
    session: AsyncSession, *, subject_id: UUID, mcp_id: UUID
) -> bool:
    return await _is_artifact_liked_by(
        session, spec=_MCPS, subject_id=subject_id, entity_id=mcp_id
    )


async def increment_skill_download_count(
    session: AsyncSession, *, skill_id: UUID
) -> None:
    """Atomic UPDATE skills SET download_count = download_count + 1."""
    await session.execute(
        update(Skill)
        .where(col(Skill.id) == skill_id)
        .values(download_count=Skill.download_count + 1)
    )


# ---------------------------------------------------------------------------
# Detail assembly (shared by the /v1/registry gateway-key routes, the
# /auth/registry browse routes, and the MCP tools)
# ---------------------------------------------------------------------------


async def _list_active_versions(
    session: AsyncSession, *, spec: ArtifactSpec, entity_id: UUID
) -> list[Any]:
    stmt = (
        select(spec.version)
        .where(
            col(spec.version_entity_fk) == entity_id,
            col(spec.version.state) == ResourceState.ACTIVE,
        )
        .order_by(col(spec.version.created_at).desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def _list_team_grants(
    session: AsyncSession, *, spec: ArtifactSpec, entity_id: UUID
) -> list[Any]:
    stmt = select(spec.grant).where(col(spec.grant_entity_fk) == entity_id)
    return list((await session.execute(stmt)).scalars().all())


async def assemble_skill_detail(
    session: AsyncSession,
    *,
    skill: Skill,
    viewer_subject_id: UUID,
    include_readme: bool = True,
    include_likes: bool = True,
) -> dict[str, Any]:
    """Build the skill detail payload: active versions (newest first), team
    grants, owner name, and optionally the artifact README and the caller's
    like state. include_readme / include_likes exist so each surface keeps its
    exact historical response shape (the /v1/registry route omits both)."""
    versions = await _list_active_versions(session, spec=_SKILLS, entity_id=skill.id)
    grants = await _list_team_grants(session, spec=_SKILLS, entity_id=skill.id)
    owner_obj = await session.get(Subject, skill.owner_subject_id)
    extra: dict[str, Any] = {}
    if include_readme:
        extra["readme"] = skill.readme
    if include_likes:
        extra["liked_by_me"] = await is_skill_liked_by(
            session, subject_id=viewer_subject_id, skill_id=skill.id
        )
    return skill_detail(
        skill,
        versions,
        grants,
        owner_name=owner_obj.name if owner_obj else None,
        **extra,
    )


async def assemble_mcp_detail(
    session: AsyncSession,
    *,
    mcp: MCP,
    viewer_subject_id: UUID,
    include_readme: bool = True,
    include_likes: bool = True,
) -> dict[str, Any]:
    """Build the MCP detail payload: active versions (newest first), team
    grants, resolved latest version, owner name, and optionally the README and
    the caller's like state. env/headers are redacted unless the viewer is the
    owner. include_readme / include_likes exist so each surface keeps its exact
    historical response shape (the /v1/registry route omits liked_by_me)."""
    versions = await _list_active_versions(session, spec=_MCPS, entity_id=mcp.id)
    grants = await _list_team_grants(session, spec=_MCPS, entity_id=mcp.id)
    latest = await _get_latest_active_version(session, spec=_MCPS, artifact=mcp)
    owner_obj = await session.get(Subject, mcp.owner_subject_id)
    # Owner sees cleartext env/headers; grantees + strangers see redacted.
    reveal = mcp.owner_subject_id == viewer_subject_id
    extra: dict[str, Any] = {}
    if include_readme:
        extra["readme"] = mcp.readme
    if include_likes:
        extra["liked_by_me"] = await is_mcp_liked_by(
            session, subject_id=viewer_subject_id, mcp_id=mcp.id
        )
    return mcp_detail(
        mcp,
        versions,
        latest,
        grants,
        owner_name=owner_obj.name if owner_obj else None,
        reveal=reveal,
        **extra,
    )
