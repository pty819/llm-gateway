from __future__ import annotations

import gzip

from llm_gateway.services import duckdb_analytics


def test_duckdb_platform_maps_linux_x64(monkeypatch):
    monkeypatch.setattr(duckdb_analytics.platform, "system", lambda: "Linux")
    monkeypatch.setattr(duckdb_analytics.platform, "machine", lambda: "x86_64")

    assert duckdb_analytics._duckdb_platform() == "linux_amd64"


def test_duckdb_platform_maps_macos_arm(monkeypatch):
    monkeypatch.setattr(duckdb_analytics.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(duckdb_analytics.platform, "machine", lambda: "arm64")

    assert duckdb_analytics._duckdb_platform() == "osx_arm64"


def test_local_duckdb_extension_is_decompressed_for_current_platform(
    tmp_path, monkeypatch
):
    platform_name = "linux_amd64"
    vendor_root = tmp_path / "vendor"
    source = (
        vendor_root
        / duckdb_analytics._DUCKDB_VERSION
        / platform_name
        / "postgres_scanner.duckdb_extension.gz"
    )
    source.parent.mkdir(parents=True)
    with gzip.open(source, "wb") as file:
        file.write(b"duckdb-extension")

    install_root = tmp_path / "home"
    monkeypatch.setattr(duckdb_analytics, "_VENDOR_EXTENSION_ROOT", vendor_root)
    monkeypatch.setattr(duckdb_analytics, "_duckdb_platform", lambda: platform_name)
    monkeypatch.setattr(
        duckdb_analytics,
        "_duckdb_extension_dir",
        lambda _: install_root / duckdb_analytics._DUCKDB_VERSION / platform_name,
    )

    target = duckdb_analytics._ensure_local_postgres_extension()

    assert target is not None
    assert target.read_bytes() == b"duckdb-extension"
    assert platform_name in target.as_posix()
