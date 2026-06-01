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
    redis_url: str = Field(
        default="redis://localhost:6379/0", alias="LLM_GATEWAY_REDIS_URL"
    )
    trusted_proxy_headers: bool = Field(
        default=False, alias="LLM_GATEWAY_TRUST_PROXY_HEADERS"
    )
    trusted_proxy_cidrs: str = Field(
        default="127.0.0.0/8,::1/128", alias="LLM_GATEWAY_TRUST_PROXY_CIDRS"
    )
    rate_limit_fail_closed: bool = Field(
        default=True, alias="LLM_GATEWAY_RATE_LIMIT_FAIL_CLOSED"
    )
    default_request_limit_per_minute: int = Field(
        default=120, alias="LLM_GATEWAY_DEFAULT_RPM"
    )
    default_concurrency_limit: int = Field(
        default=8, alias="LLM_GATEWAY_DEFAULT_CONCURRENCY"
    )
    request_fact_timeout_seconds: int = Field(
        default=30, alias="LLM_GATEWAY_FACT_TIMEOUT_SECONDS"
    )
    admin_token: str = Field(default="dev-admin-token", alias="LLM_GATEWAY_ADMIN_TOKEN")
    bootstrap_admin_username: str = Field(
        default="admin", alias="LLM_GATEWAY_BOOTSTRAP_ADMIN_USERNAME"
    )
    bootstrap_admin_password: str = Field(
        default="dev-admin-password", alias="LLM_GATEWAY_BOOTSTRAP_ADMIN_PASSWORD"
    )
    session_ttl_hours: int = Field(default=168, alias="LLM_GATEWAY_SESSION_TTL_HOURS")


@lru_cache
def get_settings() -> Settings:
    return Settings()
