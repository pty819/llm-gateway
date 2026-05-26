"""Isolated connectivity check for PostgreSQL and Redis."""

import asyncio
import inspect
import os
import socket
import sys
import time

PG_HOST = os.environ.get("LLM_GATEWAY_CHECK_PG_HOST", "localhost")
PG_PORT = int(os.environ.get("LLM_GATEWAY_CHECK_PG_PORT", "5432"))
PG_USER = os.environ.get("LLM_GATEWAY_CHECK_PG_USER", "postgres")
PG_PASS = os.environ.get("LLM_GATEWAY_CHECK_PG_PASSWORD", "")
PG_DB = os.environ.get("LLM_GATEWAY_CHECK_PG_DATABASE", "postgres")

REDIS_HOST = os.environ.get("LLM_GATEWAY_CHECK_REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("LLM_GATEWAY_CHECK_REDIS_PORT", "6379"))


def check_tcp(host: str, port: int, name: str) -> bool:
    for attempt in range(3):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        try:
            s.connect((host, port))
            print(f"  [{name}] TCP {host}:{port} OK (attempt {attempt + 1})")
            return True
        except OSError as e:
            print(f"  [{name}] TCP {host}:{port} FAIL: {e} (attempt {attempt + 1})")
        finally:
            s.close()
        time.sleep(1)
    return False


async def check_asyncpg() -> bool:
    try:
        import asyncpg
    except ImportError:
        print("  [PostgreSQL] asyncpg not installed, skipping")
        return False
    if not PG_PASS:
        print("  [PostgreSQL] LLM_GATEWAY_CHECK_PG_PASSWORD is not set")
        return False
    url = f"postgresql://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{PG_DB}"
    try:
        conn = await asyncpg.connect(url)
        version = await conn.fetchval("SELECT version()")
        await conn.close()
        print(f"  [PostgreSQL] asyncpg OK: {version[:60]}...")
        return True
    except Exception as e:
        print(f"  [PostgreSQL] asyncpg FAIL: {e}")
        return False


async def check_redis() -> bool:
    try:
        import redis.asyncio as aioredis
    except ImportError:
        print("  [Redis] redis-py not installed, skipping")
        return False
    try:
        r = aioredis.from_url(f"redis://{REDIS_HOST}:{REDIS_PORT}/0")
        pong_result = r.ping()
        pong = await pong_result if inspect.isawaitable(pong_result) else pong_result
        info = await r.info("server")
        await r.aclose()
        ver = info.get("redis_version", "unknown")
        print(f"  [Redis] OK: PONG={pong}, version={ver}")
        return pong is True
    except Exception as e:
        print(f"  [Redis] FAIL: {e}")
        return False


async def main():
    print("=== Connectivity Check ===\n")

    print("[1] TCP reachability")
    pg_tcp = check_tcp(PG_HOST, PG_PORT, "PostgreSQL")
    redis_tcp = check_tcp(REDIS_HOST, REDIS_PORT, "Redis")

    if not pg_tcp and not redis_tcp:
        print("\nBoth TCP ports unreachable. Check network / firewall / services.")
        sys.exit(1)

    print("\n[2] Protocol-level checks")
    results = {}
    if pg_tcp:
        results["PostgreSQL"] = await check_asyncpg()
    if redis_tcp:
        results["Redis"] = await check_redis()

    print("\n=== Summary ===")
    all_ok = True
    for svc, ok in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  {svc}: {status}")
        if not ok:
            all_ok = False

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
