from llm_gateway.services.facts import _redact_audit_detail


def test_redact_scrubs_sensitive_top_level_keys():
    out = _redact_audit_detail(
        {
            "api_key_value": "sk-secret",
            "api_key_ref": "vault://upstream",
            "base_url": "http://gpu-a:8000",
            "name": "qwen3-gpu-a",
        }
    )
    assert out["api_key_value"] == "<redacted>"
    assert out["api_key_ref"] == "<redacted>"
    assert out["base_url"] == "http://gpu-a:8000"
    assert out["name"] == "qwen3-gpu-a"


def test_redact_descends_into_nested_dicts_and_lists():
    out = _redact_audit_detail(
        {
            "extra_headers": {"Authorization": "Bearer sk-abc", "X-Trace": "ok"},
            "rows": [{"password": "p", "keep": 1}],
        }
    )
    assert out["extra_headers"]["Authorization"] == "<redacted>"
    assert out["extra_headers"]["X-Trace"] == "ok"
    assert out["rows"][0]["password"] == "<redacted>"
    assert out["rows"][0]["keep"] == 1


def test_redact_handles_none_and_scalars():
    assert _redact_audit_detail(None) is None
    assert _redact_audit_detail("plain") == "plain"
    assert _redact_audit_detail(42) == 42
