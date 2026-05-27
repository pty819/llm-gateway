# PRD: DuckDB Admin Heavy Analytics

## Objective

Give administrators a lightweight OLAP path for manual, heavy usage analysis
without moving normal gateway behavior, user self-service pages, auth, policy, or
request fact writes away from PostgreSQL.

## Product Decision

DuckDB is for administrator-triggered heavy analysis only. User self-usage pages
remain PostgreSQL-backed because they are short-window, subject-scoped, freshness
critical, and permission-sensitive. Reusing DuckDB for the user page would add
refresh-lag semantics and file-concurrency concerns for little benefit.

## Users

- Administrator: refreshes a local DuckDB mirror and runs long-window
  time-bucket/drilldown analysis.
- Normal user: continues to see recent personal request/token totals from
  PostgreSQL.
- Operator/developer: runs one upgrade command and one local start command.

## Scope

### In Scope

- Add DuckDB as a Python dependency.
- Add configuration for DuckDB analytics file location.
- Add a backend DuckDB analytics service that:
  - creates the DuckDB schema;
  - copies request fact rows from PostgreSQL into DuckDB;
  - denormalizes subject/project labels for report readability;
  - reports mirror status;
  - answers admin time-bucket and drilldown queries from DuckDB.
- Add admin APIs under `/admin/analytics/duckdb/*`.
- Add a small admin UI section on the usage page for DuckDB refresh/status/query.
- Add an upgrade script that syncs Python/frontend dependencies and applies
  database migrations.
- Add a one-command local start script that can optionally run upgrade first and
  start backend plus frontend.
- Add tests for DuckDB refresh/query behavior and user-usage staying PostgreSQL.

### Out Of Scope

- DuckDB in the request path.
- DuckDB for normal user self-usage pages.
- Background scheduled refresh worker.
- ClickHouse/TimescaleDB/external OLAP infrastructure.
- Arbitrary user-provided SQL execution in the web UI.
- Replacing existing PostgreSQL admin analytics endpoints.

## Functional Requirements

1. Admin can refresh the DuckDB mirror for an optional time range and optional row
   limit.
2. Refresh is idempotent for request facts already present in DuckDB.
3. Admin can view DuckDB mirror status: enabled state, path, row count, min/max
   timestamps, and file size.
4. Admin can query DuckDB time buckets with the same metric shape as the current
   PostgreSQL analytics endpoint.
5. Admin can query DuckDB drilldown by model, subject, project, endpoint, outcome,
   or streaming.
6. Existing PostgreSQL analytics and user self-usage endpoints remain available.
7. The frontend clearly labels DuckDB results as administrator heavy analysis.
8. Upgrade/start scripts are safe to rerun.

## Non-Functional Requirements

- DuckDB writes happen inside a single backend process/request; the frontend never
  writes DuckDB directly.
- DuckDB queries should not require DuckDB's Postgres extension at runtime. The
  first implementation copies through the app's existing PostgreSQL connection.
- Missing DuckDB dependency or disabled config should return an explicit service
  error, not fail import-time application startup.
- Refresh lag is visible through status/refreshed timestamps.
- User self-usage remains freshest-source PostgreSQL and does not wait for DuckDB
  refresh.

## Acceptance Criteria

- `POST /admin/analytics/duckdb/refresh` copies seeded request facts.
- `GET /admin/analytics/duckdb/time-buckets` returns bucket rows after refresh.
- `GET /admin/analytics/duckdb/drilldown` returns dimension rows after refresh.
- `/auth/usage/summary` continues to return PostgreSQL-backed personal usage.
- `scripts/upgrade_local.py --skip-frontend-install` applies backend dependency
  sync and database migrations without manual SQL.
- `scripts/start_local.py --help` documents the one-command local start path.
- Backend tests pass for DuckDB analytics and existing capacity analytics.
