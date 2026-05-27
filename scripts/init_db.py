import sys
import asyncio
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

LEGACY_SCHEMA_REVISION = "20260526_0004"


async def _schema_state() -> tuple[bool, bool]:
    from llm_gateway.core.config import get_settings

    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "select "
                    "to_regclass('public.subjects') is not null as has_subjects, "
                    "to_regclass('public.alembic_version') is not null as has_alembic_version"
                )
            )
            row = result.one()
            return bool(row.has_subjects), bool(row.has_alembic_version)
    finally:
        await engine.dispose()


def main() -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(ROOT / "alembic.ini"))
    has_subjects, has_alembic_version = asyncio.run(_schema_state())
    if has_subjects and not has_alembic_version:
        command.stamp(config, LEGACY_SCHEMA_REVISION)
    command.upgrade(config, "head")


if __name__ == "__main__":
    main()
