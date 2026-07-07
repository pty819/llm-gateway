from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "LLM Gateway"
    environment: str = "local"
    database_url: str = Field(
        default="postgresql+asyncpg://llm_gateway:llm_gateway@localhost:5432/llm_gateway",
        alias="LLM_GATEWAY_DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="LLM_GATEWAY_REDIS_URL")
    trusted_proxy_headers: bool = Field(default=True, alias="LLM_GATEWAY_TRUST_PROXY_HEADERS")
    trusted_proxy_cidrs: str = Field(
        default="127.0.0.0/8,::1/128", alias="LLM_GATEWAY_TRUST_PROXY_CIDRS"
    )
    rate_limit_fail_closed: bool = Field(default=True, alias="LLM_GATEWAY_RATE_LIMIT_FAIL_CLOSED")
    default_request_limit_per_minute: int = Field(default=120, alias="LLM_GATEWAY_DEFAULT_RPM")
    default_concurrency_limit: int = Field(default=8, alias="LLM_GATEWAY_DEFAULT_CONCURRENCY")
    request_fact_timeout_seconds: int = Field(default=30, alias="LLM_GATEWAY_FACT_TIMEOUT_SECONDS")
    # Seconds between SSE keepalive comment frames sent to the client while the
    # upstream is silent (e.g. during long reasoning). Keeps the gateway->client
    # leg alive across proxies/dev servers that drop idle streaming connections.
    stream_keepalive_seconds: float = Field(
        default=15.0, alias="LLM_GATEWAY_STREAM_KEEPALIVE_SECONDS"
    )
    # Total timeout applied to upstream model calls (forwarded to upstream
    # via httpx2; see services/upstream_client.py).
    upstream_timeout_seconds: float = Field(
        default=6000.0, alias="LLM_GATEWAY_UPSTREAM_TIMEOUT_SECONDS"
    )
    # DB connection pool sizing for the async engine.
    db_pool_size: int = Field(default=20, alias="LLM_GATEWAY_DB_POOL_SIZE")
    db_max_overflow: int = Field(default=40, alias="LLM_GATEWAY_DB_MAX_OVERFLOW")
    db_pool_recycle_seconds: int = Field(default=1800, alias="LLM_GATEWAY_DB_POOL_RECYCLE_SECONDS")
    # Optional separate (read-only replica) DSN for heavy analytics; falls
    # back to the main database_url when unset.
    analytics_database_url: str | None = Field(
        default=None, alias="LLM_GATEWAY_ANALYTICS_DATABASE_URL"
    )
    # Optional separate DSN for pytest. When set, conftest builds a dedicated
    # engine against this DB and truncates all tables between tests, so the dev
    # DB (.env.local's database_url) is never touched by the test suite. When
    # unset, the entire test suite skips (see tests/conftest.py).
    test_database_url: str | None = Field(
        default=None,
        alias="LLM_GATEWAY_TEST_DATABASE_URL",
        description="Optional separate DSN for pytest. When set, conftest creates "
        "a dedicated engine against this DB and truncates all tables between "
        "tests, so the dev DB is never touched by the test suite.",
    )
    # statement_timeout applied to analytics queries so a runaway aggregate
    # cannot monopolize the analytics connection.
    analytics_statement_timeout_seconds: float = Field(
        default=15.0, alias="LLM_GATEWAY_ANALYTICS_STATEMENT_TIMEOUT_SECONDS"
    )
    admin_token: str = Field(default="dev-admin-token", alias="LLM_GATEWAY_ADMIN_TOKEN")
    bootstrap_admin_username: str = Field(
        default="admin", alias="LLM_GATEWAY_BOOTSTRAP_ADMIN_USERNAME"
    )
    bootstrap_admin_password: str = Field(
        default="dev-admin-password", alias="LLM_GATEWAY_BOOTSTRAP_ADMIN_PASSWORD"
    )
    # When True, the gateway refuses to start if admin_token / bootstrap admin
    # password are still the shipped defaults. Defaults to environment != "local".
    require_nondefault_admin_credentials: bool | None = Field(
        default=None, alias="LLM_GATEWAY_REQUIRE_NONDEFAULT_ADMIN_CREDENTIALS"
    )
    session_ttl_hours: int = Field(default=168, alias="LLM_GATEWAY_SESSION_TTL_HOURS")
    marketplace_skill_max_bytes: int = Field(
        default=10 * 1024 * 1024, alias="LLM_GATEWAY_MARKETPLACE_SKILL_MAX_BYTES"
    )
    marketplace_list_default_size: int = Field(
        default=30, alias="LLM_GATEWAY_MARKETPLACE_LIST_DEFAULT_SIZE"
    )
    marketplace_list_max_size: int = Field(
        default=100, alias="LLM_GATEWAY_MARKETPLACE_LIST_MAX_SIZE"
    )
    # 后台健康巡检：周期探测每个 ACTIVE upstream 的 /models，故障自动禁用。
    # interval/timeout 默认 3s：发现延迟 ≤ 一个周期，探测本身有独立超时上限。
    # enabled 总开关供调试/排障时一键关闭。
    health_check_interval_seconds: float = Field(
        default=3.0, alias="LLM_GATEWAY_HEALTH_CHECK_INTERVAL_SECONDS"
    )
    health_check_timeout_seconds: float = Field(
        default=3.0, alias="LLM_GATEWAY_HEALTH_CHECK_TIMEOUT_SECONDS"
    )
    health_check_enabled: bool = Field(default=True, alias="LLM_GATEWAY_HEALTH_CHECK_ENABLED")
    # TTL on the Redis UNHEALTHY marker. A failed probe refreshes it; a passing
    # probe deletes it; if the sidecar dies the marker expires on its own so the
    # upstream auto-recovers without human intervention ("能用就行"). Must exceed
    # health_check_interval_seconds so a single missed cycle doesn't let a
    # genuinely-down upstream flip back to healthy between probes.
    health_check_unhealthy_ttl_seconds: int = Field(
        default=30, alias="LLM_GATEWAY_HEALTH_CHECK_UNHEALTHY_TTL_SECONDS"
    )
    # Quorum floor: when this many upstreams fail in a single cycle, the checker
    # treats it as a checker-side incident (event-loop freeze, network blip) and
    # skips batch-marking rather than taking out a fleet of cross-machine,
    # cross-model upstreams that are unlikely to have failed simultaneously.
    health_check_quorum_min: int = Field(default=2, alias="LLM_GATEWAY_HEALTH_CHECK_QUORUM_MIN")

    def should_require_nondefault_admin_credentials(self) -> bool:
        if self.require_nondefault_admin_credentials is not None:
            return self.require_nondefault_admin_credentials
        return self.environment != "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
