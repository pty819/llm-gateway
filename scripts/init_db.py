import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> None:
    from alembic.config import Config

    from alembic import command

    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")


if __name__ == "__main__":
    main()
