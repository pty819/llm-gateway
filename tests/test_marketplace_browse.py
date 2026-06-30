from __future__ import annotations

import io
import zipfile

import pytest

from sqlmodel import col
from sqlmodel import select as sqlselect

from llm_gateway.db.models import Subject, Team
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.services.registry import (
    create_or_append_mcp_version,
    ensure_mcp_team_grant,
    ensure_skill_team_grant,
)

# Reuse the existing helpers/style from the sibling marketplace test modules.
# Do NOT redefine them here.
from tests.test_marketplace_mcps import _mcp_config
from tests.test_marketplace_skills import (
    _login_user_with_key,
    _make_zip,
    _unique_slug,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _grant_skill_to_guest(skill_id) -> None:
    """Grant a skill to the builtin 'guest' team so every guest member can see it."""
    async with AsyncSessionLocal() as session:
        guest = (
            await session.execute(sqlselect(Team).where(col(Team.name) == "guest"))
        ).scalar_one()
        await ensure_skill_team_grant(session, skill_id=skill_id, team_id=guest.id)
        await session.commit()


class _Owner:
    """Bundles a logged-in owner's session headers, gateway key, login username
    (used as the `{owner}` path segment) and subject id, plus the test client."""

    def __init__(self, client, sess_headers, gw_key, username, owner_id):
        self.client = client
        self.headers = sess_headers
        self.gw_key = gw_key
        self.username = username
        self.subject_id = owner_id


async def _login_owner(client) -> _Owner:
    sess_headers, gw_key, username, owner_id = await _login_user_with_key(client)
    return _Owner(client, sess_headers, gw_key, username, owner_id)


# ---------------------------------------------------------------------------
# 1. browse returns a skill shared with the guest team
# ---------------------------------------------------------------------------

async def test_browse_returns_shared_skill(client):
    owner = await _login_owner(client)
    slug = _unique_slug("shared")

    resp = await client.post(
        "/auth/registry/skills",
        headers=owner.headers,
        data={"slug": slug, "name": "Shared", "version": "1.0.0", "summary": "s"},
        files={"file": (f"{slug}.zip", _make_zip(), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    skill_id = resp.json()["skill"]["id"]
    await _grant_skill_to_guest(skill_id)

    # User B: a different self-registered user (also a guest member).
    b_headers, *_ = await _login_user_with_key(client)
    browse = await client.get("/auth/registry/skills/browse", headers=b_headers)
    assert browse.status_code == 200, browse.text
    slugs = [s["slug"] for s in browse.json()["items"]]
    assert slug in slugs, slugs


# ---------------------------------------------------------------------------
# 2. browse excludes skills the viewer is not authorized to see
# ---------------------------------------------------------------------------

async def test_browse_excludes_unauthorized(client):
    owner = await _login_owner(client)
    slug = _unique_slug("private")

    resp = await client.post(
        "/auth/registry/skills",
        headers=owner.headers,
        data={"slug": slug, "name": "Private", "version": "1.0.0", "summary": "s"},
        files={"file": (f"{slug}.zip", _make_zip(), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    # NOTE: deliberately NOT granted to anyone.

    b_headers, *_ = await _login_user_with_key(client)
    browse = await client.get("/auth/registry/skills/browse", headers=b_headers)
    assert browse.status_code == 200, browse.text
    slugs = [s["slug"] for s in browse.json()["items"]]
    assert slug not in slugs, slugs


# ---------------------------------------------------------------------------
# 3. sort by downloads then by likes
# ---------------------------------------------------------------------------

async def test_browse_sort_by_downloads_then_likes(client):
    owner = await _login_owner(client)
    slug1 = _unique_slug("dl1")
    slug2 = _unique_slug("dl2")

    for slug in (slug1, slug2):
        resp = await client.post(
            "/auth/registry/skills",
            headers=owner.headers,
            data={"slug": slug, "name": slug, "version": "1.0.0", "summary": "s"},
            files={"file": (f"{slug}.zip", _make_zip(), "application/zip")},
        )
        assert resp.status_code == 200, resp.text
        await _grant_skill_to_guest(resp.json()["skill"]["id"])

    b_headers, *_ = await _login_user_with_key(client)

    # Download skill1 once via the browse download endpoint as user B.
    dl = await client.get(
        f"/auth/registry/skills/browse/{owner.username}/{slug1}/download",
        headers=b_headers,
    )
    assert dl.status_code == 200, dl.text

    # Default sort = downloads → skill1 (download_count=1) before skill2 (=0).
    by_dl = await client.get(
        "/auth/registry/skills/browse", headers=b_headers,
    )
    assert by_dl.status_code == 200, by_dl.text
    dl_slugs = [s["slug"] for s in by_dl.json()["items"]]
    assert slug1 in dl_slugs and slug2 in dl_slugs
    assert dl_slugs.index(slug1) < dl_slugs.index(slug2), dl_slugs

    # Like skill2, then sort=likes → skill2 (like_count=1) before skill1 (=0).
    like = await client.post(
        f"/auth/registry/skills/browse/{owner.username}/{slug2}/like",
        headers=b_headers,
    )
    assert like.status_code == 200, like.text
    assert like.json()["like_count"] == 1

    by_likes = await client.get(
        "/auth/registry/skills/browse?sort=likes", headers=b_headers,
    )
    assert by_likes.status_code == 200, by_likes.text
    like_slugs = [s["slug"] for s in by_likes.json()["items"]]
    assert slug1 in like_slugs and slug2 in like_slugs
    assert like_slugs.index(slug2) < like_slugs.index(slug1), like_slugs


# ---------------------------------------------------------------------------
# 4. download increments download_count; zip bytes + sha header
# ---------------------------------------------------------------------------

async def test_download_increments_count(client):
    owner = await _login_owner(client)
    slug = _unique_slug("dl")
    payload = _make_zip("download-me")

    resp = await client.post(
        "/auth/registry/skills",
        headers=owner.headers,
        data={"slug": slug, "name": "DL", "version": "1.0.0", "summary": "s"},
        files={"file": (f"{slug}.zip", payload, "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    await _grant_skill_to_guest(resp.json()["skill"]["id"])

    b_headers, *_ = await _login_user_with_key(client)

    dl = await client.get(
        f"/auth/registry/skills/browse/{owner.username}/{slug}/download",
        headers=b_headers,
    )
    assert dl.status_code == 200, dl.text
    assert dl.content  # non-empty
    assert dl.headers.get("X-Content-SHA256")
    # The served zip should match what was uploaded.
    assert dl.content == payload

    detail = await client.get(
        f"/auth/registry/skills/browse/{owner.username}/{slug}", headers=b_headers
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["download_count"] >= 1


# ---------------------------------------------------------------------------
# 5. like lifecycle (like → detail → unlike → detail)
# ---------------------------------------------------------------------------

async def test_like_lifecycle(client):
    owner = await _login_owner(client)
    slug = _unique_slug("like")

    resp = await client.post(
        "/auth/registry/skills",
        headers=owner.headers,
        data={"slug": slug, "name": "Like", "version": "1.0.0", "summary": "s"},
        files={"file": (f"{slug}.zip", _make_zip(), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    await _grant_skill_to_guest(resp.json()["skill"]["id"])

    b_headers, *_ = await _login_user_with_key(client)
    base = f"/auth/registry/skills/browse/{owner.username}/{slug}"

    like = await client.post(f"{base}/like", headers=b_headers)
    assert like.status_code == 200, like.text
    assert like.json() == {"liked_by_me": True, "like_count": 1}

    detail = await client.get(base, headers=b_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["liked_by_me"] is True

    unlike = await client.delete(f"{base}/like", headers=b_headers)
    assert unlike.status_code == 200, unlike.text
    assert unlike.json() == {"liked_by_me": False, "like_count": 0}

    detail2 = await client.get(base, headers=b_headers)
    assert detail2.status_code == 200, detail2.text
    assert detail2.json()["liked_by_me"] is False


# ---------------------------------------------------------------------------
# 6. readme extracted from the zip's SKILL.md; not present in list summary
# ---------------------------------------------------------------------------

async def test_readme_extracted_from_zip(client):
    owner = await _login_owner(client)
    slug = _unique_slug("readme")
    readme_text = "# My Skill\nHello"
    zip_bytes = _make_zip(readme_text)  # _make_zip writes SKILL.md with the given text

    resp = await client.post(
        "/auth/registry/skills",
        headers=owner.headers,
        data={"slug": slug, "name": "Readme", "version": "1.0.0", "summary": "s"},
        files={"file": (f"{slug}.zip", zip_bytes, "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    await _grant_skill_to_guest(resp.json()["skill"]["id"])

    b_headers, *_ = await _login_user_with_key(client)

    detail = await client.get(
        f"/auth/registry/skills/browse/{owner.username}/{slug}", headers=b_headers
    )
    assert detail.status_code == 200, detail.text
    assert "Hello" in (detail.json().get("readme") or "")

    # List summary must NOT include the readme field (only detail does).
    browse = await client.get("/auth/registry/skills/browse", headers=b_headers)
    assert browse.status_code == 200, browse.text
    matching = [s for s in browse.json()["items"] if s["slug"] == slug]
    assert matching, [s["slug"] for s in browse.json()["items"]]
    assert "readme" not in matching[0]


# ---------------------------------------------------------------------------
# 7. browse MCP detail redacts env/headers for non-owner
# ---------------------------------------------------------------------------

async def test_browse_mcp_redacted(client):
    owner = await _login_owner(client)
    slug = _unique_slug("mcp-redact")
    secret_env_value = "super-secret-env-value-xyz"
    secret_header_value = "bearer-secret-header-value-abc"
    cfg = _mcp_config(
        env={"API_KEY": secret_env_value},
        headers={"Authorization": secret_header_value},
    )

    # Publish the MCP as owner and grant to the guest team.
    async with AsyncSessionLocal() as session:
        owner_subject = await session.get(Subject, owner.subject_id)
        mcp = await create_or_append_mcp_version(
            session, actor=owner_subject, slug=slug, name="Redact MCP",
            version="1.0.0", summary="s", description=None, notes=None, config=cfg,
        )
        guest = (
            await session.execute(sqlselect(Team).where(col(Team.name) == "guest"))
        ).scalar_one()
        await ensure_mcp_team_grant(session, mcp_id=mcp.id, team_id=guest.id)
        await session.commit()

    # User B (non-owner, guest member) browses the MCP detail.
    b_headers, *_ = await _login_user_with_key(client)
    detail = await client.get(
        f"/auth/registry/mcps/browse/{owner.username}/{slug}", headers=b_headers
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()

    latest = body["latest"]
    # Cleartext secrets must NOT leak to a non-owner.
    assert secret_env_value not in str(body)
    assert secret_header_value not in str(body)
    # Values are redacted to "***".
    assert latest["env"] == {"API_KEY": "***"}, latest["env"]
    assert latest["headers"] == {"Authorization": "***"}, latest["headers"]
    for v in body["versions"]:
        assert v["env"] == {"API_KEY": "***"}
        assert v["headers"] == {"Authorization": "***"}


# ---------------------------------------------------------------------------
# 8. mcp readme (user-filled) is published and viewable in browse detail
# ---------------------------------------------------------------------------

async def test_mcp_readme_published_and_viewable(client):
    owner = await _login_owner(client)
    slug = _unique_slug("mcp-readme")
    readme_text = "# My MCP\nThis is a test"

    # Publish the MCP as owner with a readme field, then grant to the guest team.
    resp = await client.post(
        "/auth/registry/mcps",
        headers=owner.headers,
        json={
            "slug": slug,
            "name": "Readme MCP",
            "version": "1.0.0",
            "summary": "s",
            "readme": readme_text,
            "config": _mcp_config(),
        },
    )
    assert resp.status_code == 200, resp.text
    mcp_id = resp.json()["mcp"]["id"]

    async with AsyncSessionLocal() as session:
        guest = (
            await session.execute(sqlselect(Team).where(col(Team.name) == "guest"))
        ).scalar_one()
        await ensure_mcp_team_grant(session, mcp_id=mcp_id, team_id=guest.id)
        await session.commit()

    # User B (non-owner, guest member) browses the MCP detail.
    b_headers, *_ = await _login_user_with_key(client)
    detail = await client.get(
        f"/auth/registry/mcps/browse/{owner.username}/{slug}", headers=b_headers
    )
    assert detail.status_code == 200, detail.text
    assert "This is a test" in (detail.json().get("readme") or "")
