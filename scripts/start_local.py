from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from ipaddress import ip_address
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upgrade and start the local LLM Gateway backend and frontend."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host for both services.")
    parser.add_argument("--backend-port", type=int, default=18080)
    parser.add_argument("--frontend-port", type=int, default=5173)
    parser.add_argument(
        "--skip-upgrade",
        action="store_true",
        help="Start services without running scripts/upgrade_local.py first.",
    )
    parser.add_argument(
        "--skip-frontend-install",
        action="store_true",
        help="Forwarded to upgrade_local.py when upgrade is enabled.",
    )
    args = parser.parse_args()

    if not args.skip_upgrade:
        upgrade_cmd = ["uv", "run", "python", "scripts/upgrade_local.py"]
        if args.skip_frontend_install:
            upgrade_cmd.append("--skip-frontend-install")
        run(upgrade_cmd, cwd=ROOT)

    env = os.environ.copy()
    env.setdefault("LLM_GATEWAY_TRUST_PROXY_HEADERS", "true")
    env.setdefault("LLM_GATEWAY_TRUST_PROXY_CIDRS", _local_proxy_cidrs(args.host))
    env.setdefault("LLM_GATEWAY_BACKEND_URL", f"http://{args.host}:{args.backend_port}")

    backend = subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            "llm_gateway.main:app",
            "--host",
            args.host,
            "--port",
            str(args.backend_port),
            "--reload",
        ],
        cwd=ROOT,
        env=env,
    )
    frontend = subprocess.Popen(
        [
            "npm",
            "run",
            "dev",
            "--",
            "--host",
            args.host,
            "--port",
            str(args.frontend_port),
        ],
        cwd=FRONTEND,
        env=env,
    )
    print(f"Backend:  http://{args.host}:{args.backend_port}")
    print(f"Frontend: http://{args.host}:{args.frontend_port}")

    try:
        exit_code = wait_for_any([backend, frontend])
    except KeyboardInterrupt:
        exit_code = 130
    finally:
        stop_processes([backend, frontend])
    raise SystemExit(exit_code)


def run(command: list[str], *, cwd: Path) -> None:
    print(f"$ {' '.join(command)}")
    subprocess.run(command, cwd=cwd, check=True)


def wait_for_any(processes: list[subprocess.Popen]) -> int:
    while True:
        for process in processes:
            code = process.poll()
            if code is not None:
                return int(code)
        time.sleep(0.5)


def stop_processes(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()


def _local_proxy_cidrs(host: str) -> str:
    cidrs = ["127.0.0.0/8", "::1/128"]
    try:
        parsed = ip_address(host)
    except ValueError:
        return ",".join(cidrs)
    if not parsed.is_unspecified and not parsed.is_loopback:
        cidrs.append(f"{parsed}/{parsed.max_prefixlen}")
    return ",".join(cidrs)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc
    except KeyboardInterrupt:
        sys.exit(130)
