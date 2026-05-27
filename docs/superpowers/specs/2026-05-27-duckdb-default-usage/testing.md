# Testing Plan

## Backend

- Extend DuckDB analytics tests to insert request facts, refresh the DuckDB mirror, and verify:
  - `/admin/usage/duckdb/summary`
  - `/admin/usage/duckdb/ranking`
  - existing DuckDB time bucket and drilldown endpoints
- Verify `total_tokens` falls back to `prompt_tokens + completion_tokens` when the recorded total is null.
- Add LiteLLM client unit tests that monkeypatch `anthropic_messages` and assert both non-streaming and streaming client paths pass `drop_params=True`.

## Frontend

- Run Svelte type/check/build after replacing the heavy refresh flow.
- Verify the usage and ranking query buttons call the new DuckDB-backed action handlers.
- Verify global user search state is removed or isolated; no page should depend on a topbar-wide user query.

## Full Verification

- Targeted pytest for changed backend tests.
- Full pytest if runtime is acceptable.
- `uvx ty check` and `uvx ruff check/format`.
- Frontend check/build and existing smoke tests where available.
- Git diff review before commit/push.
