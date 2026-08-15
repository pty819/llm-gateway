"""Admin list endpoints: server-side pagination, search pushdown, embedded names.

Guards the "no full-inventory responses" contract: every admin list returns
the paginated envelope {items,total,limit,offset}, honors q/limit/offset, and
embeds related display names so a paginated client never needs the full
subject/project/team/model inventories.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio

from llm_gateway.db.models import (
    ModelAlias,
    ModelEntitlement,
    Project,
    ProjectMembership,
    RatePolicy,
    Subject,
    SubjectType,
    Team,
    TeamMembership,
    UpstreamTarget,
)
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.services.security import create_gateway_key

pytestmark = pytest.mark.asyncio(loop_scope="session")

ADMIN = {"x-admin-token": "dev-admin-token"}


@pytest_asyncio.fixture
async def seed():
    suffix = uuid4().hex[:10]
    async with AsyncSessionLocal() as session:
        subject_a = Subject(name=f"pytest-page-alpha-{suffix}", type=SubjectType.USER)
        subject_b = Subject(name=f"pytest-page-beta-{suffix}", type=SubjectType.USER)
        session.add_all([subject_a, subject_b])
        await session.flush()

        project = Project(
            name=f"pytest-page-{suffix}", owner_subject_id=subject_a.id
        )
        personal = Project(name=f"user-{suffix}-personal", owner_subject_id=subject_b.id)
        session.add_all([project, personal])
        await session.flush()

        model_a = ModelAlias(
            alias=f"pytest-pagemodel-a-{suffix}",
            upstream_model_name=f"upstream-{suffix}",
            litellm_model=f"openai/upstream-{suffix}",
        )
        model_b = ModelAlias(
            alias=f"pytest-pagemodel-b-{suffix}",
            upstream_model_name=f"upstream-{suffix}",
            litellm_model=f"openai/upstream-{suffix}",
        )
        session.add_all([model_a, model_b])
        await session.flush()

        upstream = UpstreamTarget(
            model_alias_id=model_a.id,
            name=f"pytest-pageup-{suffix}",
            base_url="http://127.0.0.1:9",
        )
        session.add(upstream)

        team = Team(name=f"pytest-pageteam-{suffix}")
        session.add(team)
        await session.flush()

        session.add(TeamMembership(team_id=team.id, subject_id=subject_a.id))
        session.add(
            ProjectMembership(project_id=project.id, subject_id=subject_b.id)
        )
        session.add(
            ModelEntitlement(model_alias_id=model_b.id, subject_id=subject_b.id)
        )
        session.add(RatePolicy(scope="subject", scope_id=subject_a.id, requests_per_minute=11))
        await session.flush()

        key, _raw = await create_gateway_key(
            session,
            subject_id=subject_a.id,
            project_id=project.id,
            name=f"pytest-pagekey-{suffix}",
        )
        await session.commit()

        yield {
            "suffix": suffix,
            "subject_a": subject_a,
            "subject_b": subject_b,
            "project": project,
            "model_a": model_a,
            "model_b": model_b,
            "upstream": upstream,
            "team": team,
            "key": key,
        }


def _envelope(body: dict) -> None:
    assert set(body) == {"items", "total", "limit", "offset"}


async def test_model_aliases_paginate_and_search(client, seed):
    resp = await client.get(
        "/admin/model-aliases",
        headers=ADMIN,
        params={"q": seed["suffix"], "limit": 1, "offset": 0},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _envelope(body)
    assert body["total"] == 2
    assert len(body["items"]) == 1
    first_id = body["items"][0]["id"]

    page_two = await client.get(
        "/admin/model-aliases",
        headers=ADMIN,
        params={"q": seed["suffix"], "limit": 1, "offset": 1},
    )
    assert page_two.json()["items"][0]["id"] != first_id

    options = await client.get(
        "/admin/model-aliases/options", headers=ADMIN, params={"q": seed["suffix"]}
    )
    aliases = {row["alias"] for row in options.json()}
    assert seed["model_a"].alias in aliases
    assert seed["model_b"].alias in aliases


async def test_upstreams_paginate_with_embedded_alias(client, seed):
    resp = await client.get(
        "/admin/upstreams", headers=ADMIN, params={"q": seed["suffix"], "limit": 10}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _envelope(body)
    assert body["total"] == 1
    item = body["items"][0]
    assert item["name"] == seed["upstream"].name
    assert item["model_alias"] == seed["model_a"].alias
    assert item["api_key_value"] is None  # redaction preserved

    options = await client.get(
        "/admin/upstreams/options", headers=ADMIN, params={"q": seed["suffix"]}
    )
    assert any(
        row["model_alias"] == seed["model_a"].alias for row in options.json()
    )


async def test_teams_search_and_options(client, seed):
    resp = await client.get(
        "/admin/teams", headers=ADMIN, params={"q": seed["suffix"], "limit": 10}
    )
    body = resp.json()
    _envelope(body)
    assert body["total"] >= 1
    assert any(row["name"] == seed["team"].name for row in body["items"])

    options = await client.get(
        "/admin/teams/options", headers=ADMIN, params={"q": seed["suffix"]}
    )
    assert {row["name"] for row in options.json()} == {seed["team"].name}


async def test_team_token_quotas_paginate_with_team_name(client, seed):
    put = await client.put(
        f"/admin/teams/{seed['team'].id}/token-quota",
        headers=ADMIN,
        json={"morning_tokens": 100},
    )
    assert put.status_code == 200, put.text

    resp = await client.get(
        "/admin/team-token-quotas",
        headers=ADMIN,
        params={"q": seed["suffix"], "limit": 10},
    )
    body = resp.json()
    _envelope(body)
    assert body["total"] == 1
    assert body["items"][0]["team_name"] == seed["team"].name


async def test_team_memberships_filter_and_embedded_names(client, seed):
    resp = await client.get(
        "/admin/team-memberships",
        headers=ADMIN,
        params={"team_id": str(seed["team"].id), "limit": 10},
    )
    body = resp.json()
    _envelope(body)
    assert body["total"] == 1
    item = body["items"][0]
    assert item["team_name"] == seed["team"].name
    assert item["subject_name"] == seed["subject_a"].name

    searched = await client.get(
        "/admin/team-memberships",
        headers=ADMIN,
        params={"q": seed["suffix"], "limit": 10},
    )
    assert searched.json()["total"] >= 1


async def test_model_team_grants_filter_and_embedded_names(client, seed):
    grant = await client.post(
        "/admin/model-team-grants",
        headers=ADMIN,
        json={
            "model_alias_id": str(seed["model_a"].id),
            "team_id": str(seed["team"].id),
        },
    )
    assert grant.status_code == 200, grant.text

    resp = await client.get(
        "/admin/model-team-grants",
        headers=ADMIN,
        params={"team_id": str(seed["team"].id), "limit": 10},
    )
    body = resp.json()
    _envelope(body)
    assert body["total"] >= 1
    item = next(
        row
        for row in body["items"]
        if row["model_alias_id"] == str(seed["model_a"].id)
    )
    assert item["model_alias"] == seed["model_a"].alias
    assert item["team_name"] == seed["team"].name


async def test_model_entitlements_filter_and_embedded_names(client, seed):
    resp = await client.get(
        "/admin/model-entitlements",
        headers=ADMIN,
        params={"subject_id": str(seed["subject_b"].id), "limit": 10},
    )
    body = resp.json()
    _envelope(body)
    assert body["total"] == 1
    item = body["items"][0]
    assert item["model_alias"] == seed["model_b"].alias
    assert item["subject_name"] == seed["subject_b"].name
    assert item["project_name"] is None
    assert item["key_name"] is None


async def test_projects_search_embedded_owner_and_options(client, seed):
    resp = await client.get(
        "/admin/projects", headers=ADMIN, params={"q": seed["suffix"], "limit": 10}
    )
    body = resp.json()
    _envelope(body)
    assert body["total"] == 2  # regular + user-* personal project
    owned = next(
        row for row in body["items"] if row["name"] == seed["project"].name
    )
    assert owned["owner_name"] == seed["subject_a"].name

    options = await client.get(
        "/admin/projects/options", headers=ADMIN, params={"q": seed["suffix"]}
    )
    names = {row["name"] for row in options.json()}
    assert f"user-{seed['suffix']}-personal" in names

    filtered = await client.get(
        "/admin/projects/options",
        headers=ADMIN,
        params={"q": seed["suffix"], "exclude_name_prefix": "user-"},
    )
    filtered_names = {row["name"] for row in filtered.json()}
    assert f"user-{seed['suffix']}-personal" not in filtered_names
    assert seed["project"].name in filtered_names


async def test_project_memberships_filter_and_embedded_names(client, seed):
    resp = await client.get(
        "/admin/project-memberships",
        headers=ADMIN,
        params={"project_id": str(seed["project"].id), "limit": 10},
    )
    body = resp.json()
    _envelope(body)
    assert body["total"] == 1
    item = body["items"][0]
    assert item["project_name"] == seed["project"].name
    assert item["subject_name"] == seed["subject_b"].name


async def test_gateway_keys_search_filter_and_embedded_names(client, seed):
    resp = await client.get(
        "/admin/gateway-keys",
        headers=ADMIN,
        params={"q": seed["suffix"], "limit": 10},
    )
    body = resp.json()
    _envelope(body)
    assert body["total"] == 1
    item = body["items"][0]
    assert item["name"] == seed["key"].name
    assert item["subject_name"] == seed["subject_a"].name
    assert item["project_name"] == seed["project"].name
    assert item["key_hash"] is None  # redaction preserved

    by_project = await client.get(
        "/admin/gateway-keys",
        headers=ADMIN,
        params={"project_id": str(seed["project"].id), "limit": 10},
    )
    assert by_project.json()["total"] >= 1


async def test_subject_options(client, seed):
    resp = await client.get(
        "/admin/subjects/options", headers=ADMIN, params={"q": seed["suffix"]}
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert {row["name"] for row in rows} == {
        seed["subject_a"].name,
        seed["subject_b"].name,
    }
    assert set(rows[0]) == {"id", "name", "login_username"}


async def test_rate_policies_paginate_with_scope_name(client, seed):
    resp = await client.get(
        "/admin/rate-policies", headers=ADMIN, params={"scope": "subject", "limit": 500}
    )
    body = resp.json()
    _envelope(body)
    item = next(
        row for row in body["items"] if row["scope_id"] == str(seed["subject_a"].id)
    )
    assert item["scope_name"] == seed["subject_a"].name
    assert item["requests_per_minute"] == 11


async def test_rate_overrides_targeted_subject_ids(client, seed):
    put = await client.put(
        f"/admin/subjects/{seed['subject_a'].id}/rate-override",
        headers=ADMIN,
        json={"rpm": 7},
    )
    assert put.status_code == 200, put.text

    targeted = await client.get(
        "/admin/subjects/rate-overrides",
        headers=ADMIN,
        params={"subject_ids": str(seed["subject_a"].id)},
    )
    assert targeted.status_code == 200, targeted.text
    assert targeted.json() == {str(seed["subject_a"].id): {"rpm": 7, "concurrency": None}}

    # Clear the override so the seeded subject doesn't leak into other tests.
    clear = await client.put(
        f"/admin/subjects/{seed['subject_a'].id}/rate-override",
        headers=ADMIN,
        json={"rpm": None, "concurrency": None},
    )
    assert clear.status_code == 200
