import pytest

from llm_gateway.main import _guard_default_admin_credentials


class _Settings:
    def __init__(
        self,
        *,
        environment="local",
        admin_token="dev-admin-token",
        bootstrap_admin_password="dev-admin-password",
        require_flag=None,
    ):
        self.environment = environment
        self.admin_token = admin_token
        self.bootstrap_admin_password = bootstrap_admin_password
        self.require_nondefault_admin_credentials = require_flag

    def should_require_nondefault_admin_credentials(self) -> bool:
        if self.require_nondefault_admin_credentials is not None:
            return self.require_nondefault_admin_credentials
        return self.environment != "local"


def test_guard_allows_default_credentials_in_local_environment():
    # local must keep working out-of-the-box with shipped dev defaults.
    _guard_default_admin_credentials(_Settings())


def test_guard_rejects_default_token_outside_local():
    with pytest.raises(RuntimeError):
        _guard_default_admin_credentials(_Settings(environment="production"))


def test_guard_rejects_default_password_outside_local():
    with pytest.raises(RuntimeError):
        _guard_default_admin_credentials(
            _Settings(environment="production", admin_token="real-token")
        )


def test_guard_allows_nondefault_credentials_outside_local():
    _guard_default_admin_credentials(
        _Settings(
            environment="production",
            admin_token="real-token",
            bootstrap_admin_password="real-password",
        )
    )


def test_guard_respects_explicit_override_to_false():
    # An operator can explicitly opt out even outside local.
    _guard_default_admin_credentials(
        _Settings(environment="production", require_flag=False)
    )
