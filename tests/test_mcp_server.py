from __future__ import annotations

import json

import pytest

from sqlmodel import col
from sqlmodel import select as sqlselect

from llm_gateway.db.models import Subject, Team
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.services.registry import (
    create_or_append_skill_version,
    ensure_skill_team_grant,
)
from tests.test_backend_integration import _auth_headers
from tests.test_marketplace_skills import (
    _login_user_with_key,
    _make_zip,
    _unique_slug,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _publish_skill_and_grant_to_guest(
    owner_id, slug, *, readme_text: str = "SKILL.md content"
) -> None:
    """Publish a skill owned by owner_id and grant it to the builtin guest team.

    Mirrors the data-plane tests in test_marketplace_skills.py.
    """
    async with AsyncSessionLocal() as session:
        owner = await session.get(Subject, owner_id)
        skill = await create_or_append_skill_version(
            session,
            actor=owner,
            slug=slug,
            name="Weather",
            version="1.0.0",
            summary="weather skill",
            description=None,
            notes=None,
            zip_bytes=_make_zip(readme_text),
        )
        guest = (
            await session.execute(sqlselect(Team).where(col(Team.name) == "guest"))
        ).scalar_one()
        await ensure_skill_team_grant(session, skill_id=skill.id, team_id=guest.id)
        await session.commit()


async def _mcp_call(client, gw_key: str, method: str, params: dict | None = None):
    """POST a JSON-RPC 2.0 request to the MCP endpoint with gateway-key auth.

    The SDK app is mounted at /v1/mcp and exposes a "/" route, so the canonical
    endpoint is /v1/mcp/ (trailing slash). FastAPI's mount redirects /v1/mcp
    → /v1/mcp/ (307); MCP clients follow redirects, but the test client does not
    by default, so we POST directly to the canonical path.

    The SDK requires Accept: application/json (or text/event-stream) per the
    Streamable HTTP spec, so we send it explicitly.
    """
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    headers = {**_auth_headers(gw_key), "Accept": "application/json"}
    resp = await client.post("/v1/mcp/", json=payload, headers=headers)
    return resp


# ---------------------------------------------------------------------------
# 1. initialize
# ---------------------------------------------------------------------------


async def test_mcp_initialize(client):
    _, gw_key, _, _ = await _login_user_with_key(client)
    resp = await _mcp_call(
        client,
        gw_key,
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    assert body["result"]["protocolVersion"] == "2025-03-26"
    assert body["result"]["serverInfo"]["name"] == "llm-gateway-registry"


# ---------------------------------------------------------------------------
# 2. tools/list
# ---------------------------------------------------------------------------


async def test_mcp_tools_list(client):
    _, gw_key, _, _ = await _login_user_with_key(client)
    resp = await _mcp_call(client, gw_key, "tools/list", {})
    assert resp.status_code == 200, resp.text
    tools = resp.json()["result"]["tools"]
    assert len(tools) == 5
    names = {t["name"] for t in tools}
    assert names == {"search_skills", "get_skill", "download_skill", "list_mcps", "get_mcp"}
    for t in tools:
        assert t["description"]
        assert t["inputSchema"]["type"] == "object"
        assert "properties" in t["inputSchema"]


# ---------------------------------------------------------------------------
# 3. tools/call search_skills
# ---------------------------------------------------------------------------


async def test_mcp_search_skills(client):
    _, gw_key, _, owner_id = await _login_user_with_key(client)
    slug = _unique_slug("weather")
    await _publish_skill_and_grant_to_guest(owner_id, slug)

    resp = await _mcp_call(
        client, gw_key, "tools/call",
        {"name": "search_skills", "arguments": {"q": slug}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "result" in body, body
    text = body["result"]["content"][0]["text"]
    parsed = json.loads(text)
    slugs = [s["slug"] for s in parsed["items"]]
    assert slug in slugs, slugs


# ---------------------------------------------------------------------------
# 4. tools/call get_skill
# ---------------------------------------------------------------------------


async def test_mcp_get_skill(client):
    _, gw_key, username, owner_id = await _login_user_with_key(client)
    slug = _unique_slug("weather")
    readme_text = "# My Skill\nHello from MCP"
    await _publish_skill_and_grant_to_guest(owner_id, slug, readme_text=readme_text)

    resp = await _mcp_call(
        client, gw_key, "tools/call",
        {"name": "get_skill", "arguments": {"owner": username, "slug": slug}},
    )
    assert resp.status_code == 200, resp.text
    text = resp.json()["result"]["content"][0]["text"]
    parsed = json.loads(text)
    assert parsed["slug"] == slug
    assert "Hello from MCP" in (parsed.get("readme") or "")
    assert len(parsed["versions"]) >= 1


# ---------------------------------------------------------------------------
# 5. tools/call download_skill returns a URL (not the zip bytes)
# ---------------------------------------------------------------------------


async def test_mcp_download_skill_returns_url(client):
    _, gw_key, username, owner_id = await _login_user_with_key(client)
    slug = _unique_slug("weather")
    await _publish_skill_and_grant_to_guest(owner_id, slug)

    resp = await _mcp_call(
        client, gw_key, "tools/call",
        {"name": "download_skill", "arguments": {"owner": username, "slug": slug}},
    )
    assert resp.status_code == 200, resp.text
    text = resp.json()["result"]["content"][0]["text"]
    parsed = json.loads(text)
    assert "/v1/registry/skills/" in parsed["url"]
    assert len(parsed["sha256"]) == 64
    assert parsed["size_bytes"] > 0
    assert parsed["version"] == "1.0.0"
    # The MCP tool returns a URL + metadata, NOT the zip bytes.
    assert "content_blob" not in parsed


# ---------------------------------------------------------------------------
# 6. unauthorized without a gateway key
# ---------------------------------------------------------------------------


async def test_mcp_unauthorized_no_key(client):
    payload = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}
    resp = await client.post(
        "/v1/mcp/", json=payload, headers={"Accept": "application/json"}
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 7. unknown method
# ---------------------------------------------------------------------------


async def test_mcp_unknown_method(client):
    _, gw_key, _, _ = await _login_user_with_key(client)
    resp = await _mcp_call(client, gw_key, "foobar", {})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The SDK rejects unrecognized methods with a JSON-RPC error. The exact code
    # depends on the SDK's validation pipeline (-32600 invalid request or
    # -32602 invalid params); either way it must be an error, not a success.
    assert "error" in body, body
    assert body["error"]["code"] < 0
