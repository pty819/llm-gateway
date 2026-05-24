import hashlib
import hmac
import secrets
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_gateway.db.models import GatewayKey, Project, ResourceState, Subject, utcnow


KEY_PREFIX_LENGTH = 12


@dataclass(frozen=True)
class AuthContext:
    key: GatewayKey
    subject: Subject
    project: Project


def generate_gateway_key() -> str:
    return f"gw-{secrets.token_urlsafe(32)}"


def key_prefix(raw_key: str) -> str:
    return raw_key[:KEY_PREFIX_LENGTH]


def hash_gateway_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def verify_gateway_key(raw_key: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_gateway_key(raw_key), stored_hash)


async def authenticate_gateway_key(session: AsyncSession, raw_key: str) -> AuthContext | None:
    prefix = key_prefix(raw_key)
    result = await session.execute(select(GatewayKey).where(GatewayKey.key_prefix == prefix))
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
        if subject.state != ResourceState.ACTIVE or project.state != ResourceState.ACTIVE:
            return None
        return AuthContext(key=candidate, subject=subject, project=project)
    return None


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
