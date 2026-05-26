import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from llm_gateway.core.config import Settings
from llm_gateway.db.models import (
    GatewayKey,
    ModelAlias,
    ModelTeamGrant,
    Project,
    ResourceState,
    Subject,
    SubjectType,
    Team,
    TeamMembership,
    UserSession,
    utcnow,
)


KEY_PREFIX_LENGTH = 12
EMPLOYEE_USERNAME_PATTERN = re.compile(r"^[a-z]\d{8}$", re.IGNORECASE)


@dataclass(frozen=True)
class AuthContext:
    key: GatewayKey
    subject: Subject
    project: Project


@dataclass(frozen=True)
class UserSessionContext:
    session: UserSession
    subject: Subject


def generate_gateway_key() -> str:
    return f"gw-{secrets.token_urlsafe(32)}"


def generate_session_token() -> str:
    return f"sess-{secrets.token_urlsafe(32)}"


def key_prefix(raw_key: str) -> str:
    return raw_key[:KEY_PREFIX_LENGTH]


def hash_gateway_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def verify_gateway_key(raw_key: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_gateway_key(raw_key), stored_hash)


def normalize_username(username: str) -> str:
    return username.strip().lower()


def is_employee_username(username: str) -> bool:
    return bool(EMPLOYEE_USERNAME_PATTERN.fullmatch(normalize_username(username)))


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 210_000
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), iterations
    )
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt, digest = stored_hash.split("$", 3)
        iterations = int(iterations_raw)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), iterations
    ).hex()
    return hmac.compare_digest(candidate, digest)


async def authenticate_gateway_key(
    session: AsyncSession, raw_key: str
) -> AuthContext | None:
    prefix = key_prefix(raw_key)
    result = await session.execute(
        select(GatewayKey).where(col(GatewayKey.key_prefix) == prefix)
    )
    candidates = result.scalars().all()
    now = utcnow()
    for candidate in candidates:
        if candidate.state != ResourceState.ACTIVE:
            continue
        if candidate.expires_at and candidate.expires_at <= now:
            continue
        if not verify_gateway_key(raw_key, candidate.key_hash):
            continue
        subject = await session.get(Subject, candidate.subject_id)
        project = await session.get(Project, candidate.project_id)
        if not subject or not project:
            return None
        if (
            subject.state != ResourceState.ACTIVE
            or project.state != ResourceState.ACTIVE
        ):
            return None
        return AuthContext(key=candidate, subject=subject, project=project)
    return None


async def authenticate_user_session(
    session: AsyncSession, raw_token: str
) -> UserSessionContext | None:
    prefix = key_prefix(raw_token)
    result = await session.execute(
        select(UserSession).where(col(UserSession.token_prefix) == prefix)
    )
    candidates = result.scalars().all()
    now = utcnow()
    for candidate in candidates:
        if candidate.state != ResourceState.ACTIVE or candidate.expires_at <= now:
            continue
        if not hmac.compare_digest(hash_gateway_key(raw_token), candidate.token_hash):
            continue
        subject = await session.get(Subject, candidate.subject_id)
        if not subject or subject.state != ResourceState.ACTIVE:
            return None
        return UserSessionContext(session=candidate, subject=subject)
    return None


async def create_user_session(
    session: AsyncSession,
    *,
    subject_id: UUID,
    ttl_hours: int,
) -> tuple[UserSession, str]:
    raw_token = generate_session_token()
    user_session = UserSession(
        subject_id=subject_id,
        token_prefix=key_prefix(raw_token),
        token_hash=hash_gateway_key(raw_token),
        expires_at=utcnow() + timedelta(hours=ttl_hours),
    )
    session.add(user_session)
    await session.flush()
    return user_session, raw_token


async def revoke_user_session(session: AsyncSession, raw_token: str) -> bool:
    context = await authenticate_user_session(session, raw_token)
    if not context:
        return False
    context.session.state = ResourceState.DISABLED
    context.session.updated_at = utcnow()
    await session.flush()
    return True


async def create_gateway_key(
    session: AsyncSession,
    *,
    subject_id: UUID,
    project_id: UUID,
    name: str,
) -> tuple[GatewayKey, str]:
    raw_key = generate_gateway_key()
    gateway_key = GatewayKey(
        subject_id=subject_id,
        project_id=project_id,
        name=name,
        key_prefix=key_prefix(raw_key),
        key_hash=hash_gateway_key(raw_key),
    )
    session.add(gateway_key)
    await session.flush()
    return gateway_key, raw_key


async def get_or_create_team(
    session: AsyncSession,
    *,
    name: str,
    notes: str | None = None,
    is_builtin: bool = False,
) -> Team:
    result = await session.execute(select(Team).where(col(Team.name) == name))
    team = result.scalar_one_or_none()
    if team:
        if team.state != ResourceState.ACTIVE:
            team.state = ResourceState.ACTIVE
        if is_builtin and not team.is_builtin:
            team.is_builtin = True
        return team
    team = Team(name=name, notes=notes, is_builtin=is_builtin)
    session.add(team)
    await session.flush()
    return team


async def ensure_team_membership(
    session: AsyncSession,
    *,
    team_id: UUID,
    subject_id: UUID,
    role: str = "member",
) -> TeamMembership:
    result = await session.execute(
        select(TeamMembership).where(
            col(TeamMembership.team_id) == team_id,
            col(TeamMembership.subject_id) == subject_id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership:
        membership.state = ResourceState.ACTIVE
        membership.role = role
        membership.updated_at = utcnow()
        return membership
    membership = TeamMembership(team_id=team_id, subject_id=subject_id, role=role)
    session.add(membership)
    await session.flush()
    return membership


async def ensure_model_team_grant(
    session: AsyncSession,
    *,
    model_alias_id: UUID,
    team_id: UUID,
) -> ModelTeamGrant:
    result = await session.execute(
        select(ModelTeamGrant).where(
            col(ModelTeamGrant.model_alias_id) == model_alias_id,
            col(ModelTeamGrant.team_id) == team_id,
        )
    )
    grant = result.scalar_one_or_none()
    if grant:
        if grant.state != ResourceState.ACTIVE:
            grant.state = ResourceState.ACTIVE
            grant.updated_at = utcnow()
        return grant
    grant = ModelTeamGrant(model_alias_id=model_alias_id, team_id=team_id)
    session.add(grant)
    await session.flush()
    return grant


async def ensure_builtin_identity(session: AsyncSession, settings: Settings) -> Subject:
    guest_team = await get_or_create_team(
        session,
        name="guest",
        notes="Default team for self-registered users.",
        is_builtin=True,
    )
    admin_team = await get_or_create_team(
        session,
        name="admin",
        notes="Built-in administrators with access to all models.",
        is_builtin=True,
    )
    username = normalize_username(settings.bootstrap_admin_username)
    result = await session.execute(
        select(Subject).where(col(Subject.login_username) == username)
    )
    admin = result.scalar_one_or_none()
    if not admin:
        admin = Subject(
            name=username,
            type=SubjectType.USER,
            login_username=username,
            password_hash=hash_password(settings.bootstrap_admin_password),
            is_admin=True,
            notes="Bootstrap administrator account.",
        )
        session.add(admin)
        await session.flush()
    else:
        admin.is_admin = True
        if not admin.password_hash:
            admin.password_hash = hash_password(settings.bootstrap_admin_password)
        if admin.state != ResourceState.ACTIVE:
            admin.state = ResourceState.ACTIVE
    await ensure_team_membership(
        session, team_id=admin_team.id, subject_id=admin.id, role="admin"
    )
    await ensure_team_membership(
        session, team_id=guest_team.id, subject_id=admin.id, role="member"
    )

    models = (await session.execute(select(ModelAlias))).scalars().all()
    for model_alias in models:
        await ensure_model_team_grant(
            session, model_alias_id=model_alias.id, team_id=admin_team.id
        )

    return admin


async def create_registered_user(
    session: AsyncSession,
    *,
    username: str,
    full_name: str,
    password: str,
) -> tuple[Subject, Project, GatewayKey, str]:
    normalized = normalize_username(username)
    if not normalized:
        raise ValueError("username_required")
    if not is_employee_username(normalized):
        raise ValueError("username_must_match_employee_id")
    full_name = full_name.strip()
    if not full_name:
        raise ValueError("full_name_required")
    existing = (
        await session.execute(
            select(Subject).where(col(Subject.login_username) == normalized)
        )
    ).scalar_one_or_none()
    if existing:
        raise ValueError("username_already_registered")

    guest_team = await get_or_create_team(
        session,
        name="guest",
        notes="Default team for self-registered users.",
        is_builtin=True,
    )
    subject = Subject(
        name=full_name,
        type=SubjectType.USER,
        login_username=normalized,
        password_hash=hash_password(password),
    )
    session.add(subject)
    await session.flush()

    project = Project(
        name=f"user-{normalized}",
        owner_subject_id=subject.id,
        notes="Self-service personal project.",
    )
    session.add(project)
    await session.flush()
    await ensure_team_membership(
        session, team_id=guest_team.id, subject_id=subject.id, role="member"
    )
    key, raw_key = await create_gateway_key(
        session,
        subject_id=subject.id,
        project_id=project.id,
        name="default",
    )
    return subject, project, key, raw_key
