from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SubjectType(StrEnum):
    USER = "user"
    SERVICE = "service"


class ResourceState(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class IPPolicyMode(StrEnum):
    ALL_PASS = "all_pass"
    ALLOWLIST = "allowlist"


class RequestOutcome(StrEnum):
    SUCCESS = "success"
    AUTH_FAILURE = "auth_failure"
    POLICY_DENIAL = "policy_denial"
    RATE_LIMITED = "rate_limited"
    ADAPTER_FAILURE = "adapter_failure"
    UPSTREAM_FAILURE = "upstream_failure"
    CLIENT_CANCELLED = "client_cancelled"


class EndpointFamily(StrEnum):
    OPENAI_CHAT = "openai_chat"
    OPENAI_RESPONSES = "openai_responses"
    ANTHROPIC_MESSAGES = "anthropic_messages"


class UsageSource(StrEnum):
    LITELLM = "litellm"
    MISSING = "missing"


class TimestampMixin(SQLModel):
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Subject(TimestampMixin, table=True):
    __tablename__ = "subjects"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(index=True)
    type: SubjectType = Field(index=True)
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)
    notes: str | None = None
    login_username: str | None = Field(default=None, index=True, unique=True)
    password_hash: str | None = None
    is_admin: bool = Field(default=False, index=True)


class Project(TimestampMixin, table=True):
    __tablename__ = "projects"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(index=True, unique=True)
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)
    owner_subject_id: UUID | None = Field(
        default=None, foreign_key="subjects.id", index=True
    )
    notes: str | None = None


class ProjectMembership(TimestampMixin, table=True):
    __tablename__ = "project_memberships"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID = Field(foreign_key="projects.id", index=True)
    subject_id: UUID = Field(foreign_key="subjects.id", index=True)
    role: str = "member"


class GatewayKey(TimestampMixin, table=True):
    __tablename__ = "gateway_keys"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    subject_id: UUID = Field(foreign_key="subjects.id", index=True)
    project_id: UUID = Field(foreign_key="projects.id", index=True)
    name: str
    key_prefix: str = Field(index=True)
    key_hash: str
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)
    expires_at: datetime | None = None


class ModelAlias(TimestampMixin, table=True):
    __tablename__ = "model_aliases"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    alias: str = Field(index=True, unique=True)
    upstream_model_name: str
    litellm_model: str
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_reasoning: bool = True
    ip_policy_mode: IPPolicyMode = Field(default=IPPolicyMode.ALL_PASS)
    ip_allowlist_cidrs: list[str] = Field(default_factory=list, sa_column=Column(JSONB))
    notes: str | None = None


class ModelEntitlement(TimestampMixin, table=True):
    __tablename__ = "model_entitlements"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    subject_id: UUID | None = Field(default=None, foreign_key="subjects.id", index=True)
    project_id: UUID | None = Field(default=None, foreign_key="projects.id", index=True)
    gateway_key_id: UUID | None = Field(
        default=None, foreign_key="gateway_keys.id", index=True
    )
    model_alias_id: UUID = Field(foreign_key="model_aliases.id", index=True)
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)


class Team(TimestampMixin, table=True):
    __tablename__ = "teams"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(index=True, unique=True)
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)
    notes: str | None = None
    is_builtin: bool = Field(default=False, index=True)


class TeamMembership(TimestampMixin, table=True):
    __tablename__ = "team_memberships"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    team_id: UUID = Field(foreign_key="teams.id", index=True)
    subject_id: UUID = Field(foreign_key="subjects.id", index=True)
    role: str = "member"
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)


class ModelTeamGrant(TimestampMixin, table=True):
    __tablename__ = "model_team_grants"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    model_alias_id: UUID = Field(foreign_key="model_aliases.id", index=True)
    team_id: UUID = Field(foreign_key="teams.id", index=True)
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)


class UserSession(TimestampMixin, table=True):
    __tablename__ = "user_sessions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    subject_id: UUID = Field(foreign_key="subjects.id", index=True)
    token_prefix: str = Field(index=True)
    token_hash: str
    expires_at: datetime = Field(index=True)
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)


class UpstreamTarget(TimestampMixin, table=True):
    __tablename__ = "upstream_targets"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    model_alias_id: UUID = Field(foreign_key="model_aliases.id", index=True)
    name: str
    base_url: str
    metrics_url: str | None = None
    api_key_ref: str | None = None
    api_key_value: str | None = None
    health_path: str = "/models"
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)
    extra_headers: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSONB))


class RouterPolicy(StrEnum):
    CONSISTENT_HASH = "consistent_hash"
    CACHE_AWARE = "cache_aware"


class RouterCommandConfig(TimestampMixin, table=True):
    __tablename__ = "router_command_configs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    model_alias_id: UUID = Field(foreign_key="model_aliases.id", index=True)
    name: str
    worker_urls: list[str] = Field(default_factory=list, sa_column=Column(JSONB))
    policy: RouterPolicy = Field(default=RouterPolicy.CONSISTENT_HASH)
    host: str = "0.0.0.0"
    port: int
    extra_args: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))


class RatePolicy(TimestampMixin, table=True):
    __tablename__ = "rate_policies"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    scope: str = Field(index=True)
    scope_id: UUID = Field(index=True)
    requests_per_minute: int | None = None
    concurrency_limit: int | None = None
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)


class AuditEvent(SQLModel, table=True):
    __tablename__ = "audit_events"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    actor_subject_id: UUID | None = Field(
        default=None, foreign_key="subjects.id", index=True
    )
    action: str = Field(index=True)
    resource_type: str = Field(index=True)
    resource_id: UUID | None = Field(default=None, index=True)
    outcome: str = Field(index=True)
    detail: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))


class RequestFact(SQLModel, table=True):
    __tablename__ = "request_facts"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    request_id: str = Field(index=True)
    started_at: datetime = Field(index=True)
    ended_at: datetime = Field(index=True)
    endpoint_family: EndpointFamily = Field(index=True)
    subject_id: UUID | None = Field(default=None, foreign_key="subjects.id", index=True)
    subject_type: SubjectType | None = Field(default=None, index=True)
    project_id: UUID | None = Field(default=None, foreign_key="projects.id", index=True)
    model_alias: str | None = Field(default=None, index=True)
    upstream_target_id: UUID | None = Field(
        default=None, foreign_key="upstream_targets.id", index=True
    )
    streaming: bool = Field(default=False, index=True)
    outcome: RequestOutcome = Field(index=True)
    usage_source: UsageSource = Field(default=UsageSource.MISSING, index=True)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    latency_ms: int | None = None
    time_to_first_token_ms: int | None = None
    stream_duration_ms: int | None = None
    retry_count: int = 0
    fallback_count: int = 0
    fallback_tokens: int | None = None
    queue_ms: int | None = None
    prefill_ms: int | None = None
    decode_ms: int | None = None
    kv_cache_usage: float | None = None
    performance_detail: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB)
    )
    error_class: str | None = Field(default=None, index=True)
    error_detail: str | None = None
