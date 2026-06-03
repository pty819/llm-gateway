from __future__ import annotations

from starlette.requests import Request

from llm_gateway.api.deps import client_ip_dep
from llm_gateway.core.config import Settings


def _trusted_proxy_settings() -> Settings:
    settings = Settings()
    settings.trusted_proxy_headers = True
    settings.trusted_proxy_cidrs = "127.0.0.0/8,::1/128"
    return settings


def _request(*, client_host: str, headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/models",
            "headers": [
                (name.lower().encode("latin-1"), value.encode("latin-1"))
                for name, value in headers.items()
            ],
            "client": (client_host, 5173),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def test_client_ip_uses_forwarded_for_from_trusted_vite_proxy():
    settings = _trusted_proxy_settings()
    request = _request(
        client_host="127.0.0.1",
        headers={"x-forwarded-for": "10.21.48.65, 127.0.0.1"},
    )

    assert client_ip_dep(request, settings) == "10.21.48.65"


def test_client_ip_trusts_local_forwarded_proxy_by_default():
    request = _request(
        client_host="127.0.0.1",
        headers={"x-forwarded-for": "10.21.48.65, 127.0.0.1"},
    )

    assert client_ip_dep(request, Settings()) == "10.21.48.65"


def test_client_ip_ignores_spoofed_forwarded_for_from_untrusted_client():
    settings = _trusted_proxy_settings()
    request = _request(
        client_host="198.51.100.10",
        headers={"x-forwarded-for": "10.21.48.65"},
    )

    assert client_ip_dep(request, settings) == "198.51.100.10"


def test_client_ip_uses_real_ip_from_trusted_proxy_when_forwarded_for_absent():
    settings = _trusted_proxy_settings()
    request = _request(
        client_host="::1",
        headers={"x-real-ip": "10.21.48.66"},
    )

    assert client_ip_dep(request, settings) == "10.21.48.66"
