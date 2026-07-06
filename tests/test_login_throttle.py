import pytest

from llm_gateway.services.rate_limit import RateLimitExceeded, check_login_rate


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, int] = {}

    async def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key, ttl):
        return True


async def test_login_rate_allows_up_to_limit():
    redis = _FakeRedis()
    for _ in range(10):
        await check_login_rate(redis, client_ip="10.0.0.1", limit=10)


async def test_login_rate_blocks_over_limit():
    redis = _FakeRedis()
    for _ in range(10):
        await check_login_rate(redis, client_ip="10.0.0.2", limit=10)
    with pytest.raises(RateLimitExceeded):
        await check_login_rate(redis, client_ip="10.0.0.2", limit=10)


async def test_login_rate_is_noop_without_client_ip():
    # No IP (e.g. malformed request) must not block auth.
    await check_login_rate(_FakeRedis(), client_ip="", limit=1)


async def test_unknown_user_and_wrong_password_return_same_error(client):
    """Username enumeration via response body must be impossible: an unknown
    user and a bad password against a real user yield the identical error."""
    unknown = await client.post(
        "/auth/login", json={"username": "z99999999", "password": "wrongpass1"}
    )
    # Bootstrap admin exists in the migrated DB; a wrong password must also 401.
    wrong = await client.post("/auth/login", json={"username": "admin", "password": "wrongpass1"})
    assert unknown.status_code == 401
    assert wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"] == "invalid_login"
