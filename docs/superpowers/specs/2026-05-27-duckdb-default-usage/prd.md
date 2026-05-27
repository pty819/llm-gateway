# DuckDB Default Admin Usage PRD

## Problem

Admin usage analysis is meant to be a manual, heavyweight workflow, but the console still performs Postgres aggregations for summary, ranking, time buckets, and drilldown during normal refresh/load. On large `request_facts` tables, this can make simply opening the page or refreshing resources slow. The UI also has a duplicate DuckDB panel instead of treating DuckDB as the normal analytics engine.

A second usability bug comes from globally shared user search state: typing a fuzzy user filter in one admin surface changes selectors elsewhere. Separately, Anthropic Messages forwarding through LiteLLM can pass unsupported Claude Code parameters downstream unless `drop_params` is enabled.

## Goals

- Make DuckDB the default engine for admin manual usage summary, ranking, time bucket, and drilldown queries.
- Keep `refreshAll()` lightweight by avoiding automatic heavy usage/ranking/analytics queries.
- Use one normal "查询" flow on usage/ranking pages that refreshes the DuckDB mirror and updates the visible totals/tables.
- Preserve Postgres-backed admin endpoints for compatibility and focused fallback use.
- Scope fuzzy user-search state per form/page.
- Pass `drop_params=True` for LiteLLM Anthropic Messages calls.

## Non-goals

- Replacing user self-service usage summary with DuckDB in this iteration.
- Real-time analytics refresh.
- Removing historical Postgres endpoints.
- Adding ClickHouse or a separate analytics daemon.

## Functional Requirements

- Add DuckDB-backed admin usage summary endpoint.
- Add DuckDB-backed admin usage ranking endpoint.
- Frontend usage page query button refreshes DuckDB for the selected time range and then queries summary, time buckets, and drilldown from DuckDB.
- Frontend ranking page query button refreshes DuckDB for the selected time range and then queries ranking from DuckDB.
- Admin login/global refresh fetches resources and audit events, plus DuckDB status, but does not run heavy analytics.
- The usage page displays DuckDB mirror status in the normal analytics area, without duplicate DuckDB result tables.
- User selectors that need fuzzy search have local state and do not reuse a cross-page subject search variable.
- Anthropic Messages non-streaming and streaming calls include `drop_params=True`.

## Acceptance Criteria

- Loading admin resources does not call `/admin/usage/summary`, `/admin/usage/ranking`, `/admin/analytics/time-buckets`, or `/admin/analytics/drilldown` from the frontend.
- Manual usage/ranking actions call `/admin/analytics/duckdb/refresh` and DuckDB query endpoints.
- DuckDB summary/ranking results match the token fallback semantics already used in analytics metrics.
- Search text typed in one user selector does not filter selectors on other pages.
- Tests verify DuckDB summary/ranking and LiteLLM Anthropic `drop_params`.
