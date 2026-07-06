from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

DUCKDB_VERSION = "v1.5.3"
EXTENSION_NAME = "postgres_scanner"
DEFAULT_PLATFORMS = ("linux_amd64", "osx_arm64")
ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = ROOT / "vendor" / "duckdb" / "extensions"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch pinned DuckDB PostgreSQL extension binaries into vendor/."
    )
    parser.add_argument(
        "--platform",
        action="append",
        choices=("linux_amd64", "osx_arm64", "osx_amd64", "windows_amd64"),
        help="DuckDB platform to fetch. Defaults to linux_amd64 and osx_arm64.",
    )
    args = parser.parse_args()

    platforms = tuple(args.platform or DEFAULT_PLATFORMS)
    for platform_name in platforms:
        fetch_platform(platform_name)


def fetch_platform(platform_name: str) -> None:
    target = VENDOR_ROOT / DUCKDB_VERSION / platform_name / f"{EXTENSION_NAME}.duckdb_extension.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    url = (
        f"https://extensions.duckdb.org/{DUCKDB_VERSION}/{platform_name}/"
        f"{EXTENSION_NAME}.duckdb_extension.gz"
    )
    print(f"Fetching {url}")
    temp = target.with_name(f"{target.name}.tmp")
    with urllib.request.urlopen(url, timeout=120) as response, temp.open("wb") as file:
        file.write(response.read())
    temp.replace(target)
    print(f"Wrote {target.relative_to(ROOT)} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
