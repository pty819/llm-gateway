from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync local dependencies and apply LLM Gateway database upgrades."
    )
    parser.add_argument(
        "--skip-python-sync",
        action="store_true",
        help="Skip `uv sync` for Python dependencies.",
    )
    parser.add_argument(
        "--skip-frontend-install",
        action="store_true",
        help="Skip `npm install` in the frontend directory.",
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Skip Alembic database upgrade via scripts/init_db.py.",
    )
    args = parser.parse_args()

    if not args.skip_python_sync:
        run(["uv", "sync"], cwd=ROOT)
    if not args.skip_frontend_install:
        run(["npm", "install"], cwd=FRONTEND)
    if not args.skip_db:
        run(["uv", "run", "python", "scripts/init_db.py"], cwd=ROOT)

    print("Local upgrade complete.")


def run(command: list[str], *, cwd: Path) -> None:
    print(f"$ {' '.join(command)}")
    subprocess.run(command, cwd=cwd, check=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc
    except KeyboardInterrupt:
        sys.exit(130)
