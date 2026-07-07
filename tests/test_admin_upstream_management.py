from __future__ import annotations

from uuid import uuid4

import pytest
from conftest import fetch_request_fact

from llm_gateway.db.models import (
    EndpointFamily,
    RequestFact,
    RequestOutcome,
    utcnow,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_admin_updates_router_command_rate_policy_and_upstream_health(
    client, gateway_fixture
):
    from llm_gateway.core.config import get_settings

    headers = {"x-admin-token": get_settings().admin_token}

    health = await client.get(
        f"/admin/upstreams/{gateway_fixture.upstream_id}/health", headers=headers
    )
    assert health.status_code == 200, health.text
    health_payload = health.json()
    assert health_payload["upstream"]["api_key_value"] is None
    assert health_payload["upstream"]["has_api_key"] is True
    assert health_payload["health"]["status_code"] < 500

    alias_patch = await client.patch(
        f"/admin/model-aliases/{gateway_fixture.model_alias_id}",
        headers=headers,
        json={"notes": "updated by integration test"},
    )
    assert alias_patch.status_code == 200, alias_patch.text
    assert alias_patch.json()["notes"] == "updated by integration test"

    router_config = await client.post(
        "/admin/router-command-configs",
        headers=headers,
        json={
            "model_alias_id": str(gateway_fixture.model_alias_id),
            "name": "pytest-router",
            "worker_urls": ["http://127.0.0.1:9001", "http://127.0.0.1:9002"],
            "policy": "consistent_hash",
            "port": 19001,
            "extra_args": {"request_timeout": 30},
        },
    )
    assert router_config.status_code == 200, router_config.text
    command = router_config.json()["command"]
    assert "vllm-router" in command
    assert "--worker-urls" in command
    assert "http://127.0.0.1:9001" in command

    rate_policy = await client.post(
        "/admin/rate-policies",
        headers=headers,
        json={
            "scope": "project",
            "scope_id": str(gateway_fixture.project_id),
            "requests_per_minute": 99,
            "concurrency_limit": 7,
        },
    )
    assert rate_policy.status_code == 200, rate_policy.text
    policy_id = rate_policy.json()["id"]

    patched_policy = await client.patch(
        f"/admin/rate-policies/{policy_id}",
        headers=headers,
        json={"requests_per_minute": 55},
    )
    assert patched_policy.status_code == 200, patched_policy.text
    assert patched_policy.json()["requests_per_minute"] == 55


async def test_admin_can_edit_upstream_endpoint_after_launch(client, gateway_fixture):
    from llm_gateway.core.config import get_settings

    headers = {"x-admin-token": get_settings().admin_token}
    new_base_url = "https://example.internal/v1"
    metrics_url = "https://example.internal:29000/metrics"
    patched = await client.patch(
        f"/admin/upstreams/{gateway_fixture.upstream_id}",
        headers=headers,
        json={
            "name": "patched-upstream",
            "base_url": new_base_url,
            "metrics_url": metrics_url,
            "health_path": "/healthz",
            "api_key_ref": "patched-key-ref",
            "extra_headers": {"x-test": "patched"},
        },
    )

    assert patched.status_code == 200, patched.text
    payload = patched.json()
    assert payload["name"] == "patched-upstream"
    assert payload["base_url"] == new_base_url
    assert payload["metrics_url"] == metrics_url
    assert payload["health_path"] == "/healthz"
    assert payload["api_key_ref"] == "patched-key-ref"
    assert payload["extra_headers"] == {"x-test": "patched"}
    assert payload["api_key_value"] is None


async def test_admin_enforces_homogeneous_upstream_replicas(client):
    from llm_gateway.core.config import get_settings

    headers = {"x-admin-token": get_settings().admin_token}
    suffix = uuid4().hex
    model = await client.post(
        "/admin/model-aliases",
        headers=headers,
        json={
            "alias": f"homogeneous-{suffix}",
            "upstream_model_name": "homogeneous-upstream",
            "litellm_model": "homogeneous-upstream",
            "sticky_ttl_seconds": 1800,
        },
    )
    assert model.status_code == 200, model.text
    model_payload = model.json()
    assert model_payload["sticky_ttl_seconds"] == 1800

    first = await client.post(
        "/admin/upstreams",
        headers=headers,
        json={
            "model_alias_id": model_payload["id"],
            "name": "replica-a",
            "base_url": "http://replica-a:8000/v1",
            "api_key_value": "same-key",
            "health_path": "/models",
            "extra_headers": {"x-shared": "1"},
        },
    )
    assert first.status_code == 200, first.text

    second = await client.post(
        "/admin/upstreams",
        headers=headers,
        json={
            "model_alias_id": model_payload["id"],
            "name": "replica-b",
            "base_url": "http://replica-b:8000/v1",
            "api_key_value": "same-key",
            "health_path": "/models",
            "extra_headers": {"x-shared": "1"},
        },
    )
    assert second.status_code == 200, second.text

    mismatched = await client.post(
        "/admin/upstreams",
        headers=headers,
        json={
            "model_alias_id": model_payload["id"],
            "name": "replica-c",
            "base_url": "http://replica-c:8000/v1",
            "api_key_value": "different-key",
            "health_path": "/models",
            "extra_headers": {"x-shared": "1"},
        },
    )
    assert mismatched.status_code == 409
    assert mismatched.json()["detail"] == (
        "upstream_replicas_must_share_key_headers_and_health_path"
    )


async def test_model_alias_delete_requires_cascade_for_upstreams(client):
    from llm_gateway.core.config import get_settings

    headers = {"x-admin-token": get_settings().admin_token}
    suffix = uuid4().hex
    model = await client.post(
        "/admin/model-aliases",
        headers=headers,
        json={
            "alias": f"delete-model-{suffix}",
            "upstream_model_name": "delete-upstream-model",
            "litellm_model": "delete-upstream-model",
        },
    )
    assert model.status_code == 200, model.text
    model_id = model.json()["id"]

    upstream = await client.post(
        "/admin/upstreams",
        headers=headers,
        json={
            "model_alias_id": model_id,
            "name": f"delete-upstream-{suffix}",
            "base_url": "http://127.0.0.1:65530/v1",
        },
    )
    assert upstream.status_code == 200, upstream.text

    blocked = await client.delete(f"/admin/model-aliases/{model_id}", headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "model_alias_has_upstreams"

    deleted = await client.delete(
        f"/admin/model-aliases/{model_id}",
        headers=headers,
        params={"cascade_upstreams": True},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_upstreams"] == 1


async def test_admin_can_delete_used_upstream_without_deleting_request_facts(
    client,
):
    from llm_gateway.core.config import get_settings
    from llm_gateway.db.models import (
        ModelAlias,
        Project,
        Subject,
        SubjectType,
        UpstreamTarget,
    )
    from llm_gateway.db.session import AsyncSessionLocal

    suffix = uuid4().hex
    request_id = f"pytest-used-upstream-delete-{uuid4()}"
    async with AsyncSessionLocal() as session:
        subject = Subject(name=f"delete-upstream-subject-{suffix}", type=SubjectType.USER)
        session.add(subject)
        await session.flush()
        project = Project(name=f"delete-upstream-project-{suffix}", owner_subject_id=subject.id)
        session.add(project)
        await session.flush()
        model = ModelAlias(
            alias=f"delete-upstream-model-{suffix}",
            upstream_model_name=f"delete-upstream-model-{suffix}",
            litellm_model=f"delete-upstream-model-{suffix}",
        )
        session.add(model)
        await session.flush()
        upstream = UpstreamTarget(
            model_alias_id=model.id,
            name=f"delete-upstream-{suffix}",
            base_url="https://example.internal/v1",
            api_key_value="test-key",
        )
        session.add(upstream)
        await session.flush()
        fact = RequestFact(
            request_id=request_id,
            started_at=utcnow(),
            ended_at=utcnow(),
            endpoint_family=EndpointFamily.OPENAI_CHAT,
            subject_id=subject.id,
            project_id=project.id,
            model_alias=model.alias,
            upstream_target_id=upstream.id,
            streaming=False,
            outcome=RequestOutcome.SUCCESS,
            prompt_tokens=3,
            completion_tokens=4,
            total_tokens=7,
        )
        session.add(fact)
        await session.commit()
        upstream_id = upstream.id
        model_alias = model.alias

    headers = {"x-admin-token": get_settings().admin_token}
    deleted = await client.delete(f"/admin/upstreams/{upstream_id}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["detached_usage_facts"] >= 1

    fact = await fetch_request_fact(request_id)
    assert fact.model_alias == model_alias
    assert fact.upstream_target_id is None


async def test_admin_can_cascade_delete_used_model_alias_preserving_usage(
    client,
):
    from llm_gateway.core.config import get_settings
    from llm_gateway.db.models import (
        ModelAlias,
        Project,
        Subject,
        SubjectType,
        UpstreamTarget,
    )
    from llm_gateway.db.session import AsyncSessionLocal

    suffix = uuid4().hex
    request_id = f"pytest-used-alias-delete-{uuid4()}"
    async with AsyncSessionLocal() as session:
        subject = Subject(name=f"delete-alias-subject-{suffix}", type=SubjectType.USER)
        session.add(subject)
        await session.flush()
        project = Project(name=f"delete-alias-project-{suffix}", owner_subject_id=subject.id)
        session.add(project)
        await session.flush()
        model = ModelAlias(
            alias=f"delete-alias-model-{suffix}",
            upstream_model_name=f"delete-alias-model-{suffix}",
            litellm_model=f"delete-alias-model-{suffix}",
        )
        session.add(model)
        await session.flush()
        upstream = UpstreamTarget(
            model_alias_id=model.id,
            name=f"delete-alias-upstream-{suffix}",
            base_url="https://example.internal/v1",
            api_key_value="test-key",
        )
        session.add(upstream)
        await session.flush()
        session.add(
            RequestFact(
                request_id=request_id,
                started_at=utcnow(),
                ended_at=utcnow(),
                endpoint_family=EndpointFamily.OPENAI_RESPONSES,
                subject_id=subject.id,
                project_id=project.id,
                model_alias=model.alias,
                upstream_target_id=upstream.id,
                streaming=False,
                outcome=RequestOutcome.SUCCESS,
                prompt_tokens=5,
                completion_tokens=6,
                total_tokens=11,
            )
        )
        await session.commit()
        model_alias_id = model.id
        model_alias = model.alias

    headers = {"x-admin-token": get_settings().admin_token}
    blocked = await client.delete(f"/admin/model-aliases/{model_alias_id}", headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "model_alias_has_upstreams"

    deleted = await client.delete(
        f"/admin/model-aliases/{model_alias_id}",
        headers=headers,
        params={"cascade_upstreams": True},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_upstreams"] >= 1
    assert deleted.json()["detached_usage_facts"] >= 1

    fact = await fetch_request_fact(request_id)
    assert fact.model_alias == model_alias
    assert fact.upstream_target_id is None
