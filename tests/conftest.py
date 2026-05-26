from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from sqlalchemy import select
from sqlmodel import col


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
load_dotenv(ROOT / ".env.local")


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    from scripts.init_db import main as init_db

    init_db()


@pytest_asyncio.fixture
async def client():
    from llm_gateway.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", timeout=90
    ) as item:
        yield item


@pytest_asyncio.fixture
async def external_ip_client():
    from llm_gateway.main import app

    transport = httpx.ASGITransport(app=app, client=("198.51.100.10", 12345))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", timeout=90
    ) as item:
        yield item


@dataclass(frozen=True)
class GatewayFixture:
    raw_key: str
    model_alias: str
    subject_id: UUID
    project_id: UUID
    key_id: UUID
    model_alias_id: UUID
    upstream_id: UUID


@pytest_asyncio.fixture
async def gateway_fixture() -> GatewayFixture:
    from llm_gateway.db.models import (
        ModelAlias,
        ModelEntitlement,
        Project,
        Subject,
        SubjectType,
        UpstreamTarget,
    )
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.security import create_gateway_key

    suffix = uuid4().hex
    upstream_model = os.environ["LLM_GATEWAY_UPSTREAM_MODEL"]
    litellm_model = os.environ.get(
        "LLM_GATEWAY_LITELLM_MODEL", f"openai/{upstream_model}"
    )

    async with AsyncSessionLocal() as session:
        subject = Subject(name=f"pytest-user-{suffix}", type=SubjectType.USER)
        session.add(subject)
        await session.flush()

        project = Project(name=f"pytest-project-{suffix}", owner_subject_id=subject.id)
        session.add(project)
        await session.flush()

        model_alias = ModelAlias(
            alias=f"pytest-model-{suffix}",
            upstream_model_name=upstream_model,
            litellm_model=litellm_model,
        )
        session.add(model_alias)
        await session.flush()

        upstream = UpstreamTarget(
            model_alias_id=model_alias.id,
            name=f"pytest-upstream-{suffix}",
            base_url=os.environ["LLM_GATEWAY_UPSTREAM_BASE_URL"],
            api_key_value=os.environ["LLM_GATEWAY_UPSTREAM_API_KEY"],
        )
        session.add(upstream)
        await session.flush()

        entitlement = ModelEntitlement(
            project_id=project.id, model_alias_id=model_alias.id
        )
        session.add(entitlement)
        key, raw_key = await create_gateway_key(
            session,
            subject_id=subject.id,
            project_id=project.id,
            name=f"pytest-key-{suffix}",
        )
        await session.commit()

        return GatewayFixture(
            raw_key=raw_key,
            model_alias=model_alias.alias,
            subject_id=subject.id,
            project_id=project.id,
            key_id=key.id,
            model_alias_id=model_alias.id,
            upstream_id=upstream.id,
        )


async def fetch_request_fact(request_id: str):
    from llm_gateway.db.models import RequestFact
    from llm_gateway.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(RequestFact).where(col(RequestFact.request_id) == request_id)
        )
        return result.scalar_one()
