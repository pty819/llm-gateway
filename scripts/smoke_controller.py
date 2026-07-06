import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


async def main() -> None:
    from sqlalchemy import select
    from sqlmodel import col

    from llm_gateway.db.models import GatewayKey
    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.services.security import create_gateway_key

    async with AsyncSessionLocal() as session:
        dev_key = (
            await session.execute(select(GatewayKey).where(col(GatewayKey.name) == "dev-key"))
        ).scalar_one()
        _, raw_key = await create_gateway_key(
            session,
            subject_id=dev_key.subject_id,
            project_id=dev_key.project_id,
            name=f"smoke-key-{uuid4()}",
        )
        await session.commit()

    import httpx

    from llm_gateway.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", timeout=60
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {raw_key}"},
            json={
                "model": "dev-model",
                "messages": [{"role": "user", "content": "Reply with one short sentence."}],
                "max_tokens": 64,
                "temperature": 0,
            },
        )
        print("status", response.status_code)
        payload = response.json()
        print(
            json.dumps(
                {
                    "model": payload.get("model"),
                    "finish_reason": (payload.get("choices") or [{}])[0].get("finish_reason"),
                    "usage": payload.get("usage"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        response.raise_for_status()


if __name__ == "__main__":
    asyncio.run(main())
