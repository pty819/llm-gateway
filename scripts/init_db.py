import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


async def main() -> None:
    from sqlmodel import SQLModel

    from llm_gateway.db.models import (  # noqa: F401
        AuditEvent,
        GatewayKey,
        ModelAlias,
        ModelEntitlement,
        Project,
        ProjectMembership,
        RatePolicy,
        RequestFact,
        RouterCommandConfig,
        Subject,
        UpstreamTarget,
    )
    from llm_gateway.db.session import engine

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


if __name__ == "__main__":
    asyncio.run(main())
