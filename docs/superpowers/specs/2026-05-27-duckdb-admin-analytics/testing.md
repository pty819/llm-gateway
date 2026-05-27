# Test Spec: DuckDB Admin Heavy Analytics

## Unit/Integration Tests

1. Seed request facts with success and failure outcomes.
2. Refresh DuckDB through the admin API.
3. Assert refresh response includes copied row count, row count, path, and status.
4. Query DuckDB time buckets and assert:
   - request count includes seeded facts;
   - prompt/completion/total/cached tokens aggregate correctly;
   - latency, TTFT, stream duration, retry/fallback, and vLLM metrics are present.
5. Query DuckDB drilldown by model and assert seeded model appears.
6. Query `/auth/usage/summary` with a user session and assert the personal totals
   are returned without requiring DuckDB refresh.
7. Validate script help output for upgrade and start commands.

## Manual QA

- Open the admin Usage page.
- Set a time range.
- Refresh the DuckDB mirror.
- Run a DuckDB query and verify the displayed source/status text.
- Confirm the ordinary usage dashboard still works if the DuckDB mirror is stale.

## Regression Risks

- DuckDB import at module import time could break environments before `uv sync`.
  Test by keeping imports lazy in the service.
- DuckDB file writes from multiple workers could conflict. MVP uses manual refresh
  and documents single-writer semantics.
- Frontend could imply user usage is DuckDB-backed. Labels must keep the two paths
  separate.
- Upgrade script could run npm from the wrong directory. Use absolute project paths.
