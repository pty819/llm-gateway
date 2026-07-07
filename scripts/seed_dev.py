import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def load_dotenv(path: str = ".env.local") -> None:
    env_path = ROOT / path
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


async def main() -> None:
    load_dotenv()

    from sqlalchemy import select
    from sqlmodel import col

    from llm_gateway.db.models import (
        GatewayKey,
        ModelAlias,
        ModelEntitlement,
        Project,
        ResourceState,
        Subject,
        SubjectType,
        UpstreamTarget,
    )
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.security import create_gateway_key

    base_url = os.environ["LLM_GATEWAY_UPSTREAM_BASE_URL"]
    upstream_model = os.environ["LLM_GATEWAY_UPSTREAM_MODEL"]
    api_key = os.environ["LLM_GATEWAY_UPSTREAM_API_KEY"]

    async with AsyncSessionLocal() as session:
        subject = (
            await session.execute(select(Subject).where(col(Subject.name) == "dev-user"))
        ).scalar_one_or_none()
        if not subject:
            subject = Subject(name="dev-user", type=SubjectType.USER)
            session.add(subject)
            await session.flush()

        project = (
            await session.execute(select(Project).where(col(Project.name) == "dev-project"))
        ).scalar_one_or_none()
        if not project:
            project = Project(name="dev-project", owner_subject_id=subject.id)
            session.add(project)
            await session.flush()

        model_alias = (
            await session.execute(select(ModelAlias).where(col(ModelAlias.alias) == "dev-model"))
        ).scalar_one_or_none()
        if not model_alias:
            model_alias = ModelAlias(
                alias="dev-model",
                upstream_model_name=upstream_model,
            )
            session.add(model_alias)
            await session.flush()

        upstream = (
            await session.execute(
                select(UpstreamTarget).where(
                    col(UpstreamTarget.model_alias_id) == model_alias.id,
                    col(UpstreamTarget.name) == "dev-upstream",
                )
            )
        ).scalar_one_or_none()
        if not upstream:
            upstream = UpstreamTarget(
                model_alias_id=model_alias.id,
                name="dev-upstream",
                base_url=base_url,
                api_key_value=api_key,
            )
            session.add(upstream)
            await session.flush()
        else:
            upstream.base_url = base_url
            upstream.api_key_value = api_key
            upstream.state = ResourceState.ACTIVE

        entitlement = (
            await session.execute(
                select(ModelEntitlement).where(
                    col(ModelEntitlement.project_id) == project.id,
                    col(ModelEntitlement.model_alias_id) == model_alias.id,
                )
            )
        ).scalar_one_or_none()
        if not entitlement:
            entitlement = ModelEntitlement(project_id=project.id, model_alias_id=model_alias.id)
            session.add(entitlement)

        existing_key = (
            await session.execute(
                select(GatewayKey).where(
                    col(GatewayKey.name) == "dev-key",
                    col(GatewayKey.project_id) == project.id,
                )
            )
        ).scalar_one_or_none()
        plaintext_key = None
        if not existing_key:
            gateway_key, plaintext_key = await create_gateway_key(
                session,
                subject_id=subject.id,
                project_id=project.id,
                name="dev-key",
            )
        else:
            gateway_key = existing_key

        await session.commit()

    print("seeded dev subject/project/model/upstream")
    print("model_alias=dev-model")
    print(f"gateway_key_prefix={gateway_key.key_prefix}")
    if plaintext_key and os.environ.get("LLM_GATEWAY_PRINT_SEEDED_KEY") == "true":
        print(f"gateway_key={plaintext_key}")
    elif plaintext_key:
        print(
            "gateway_key created; set LLM_GATEWAY_PRINT_SEEDED_KEY=true only when plaintext output is needed"
        )
    else:
        print(
            "gateway_key already exists; create a new key through /admin/gateway-keys if plaintext is needed"
        )


if __name__ == "__main__":
    asyncio.run(main())
