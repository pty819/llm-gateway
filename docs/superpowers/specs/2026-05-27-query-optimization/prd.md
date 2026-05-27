# Query Optimization PRD

## Goal

Reduce analytics query cost after the admin UI moved to bounded date ranges, top-N sections, and paginated resource lists.

## Scope

- DuckDB admin usage queries should return one aggregate totals row for banner metrics and a separate limited detail list for the visible Top 5 table.
- DuckDB refresh should avoid `OFFSET` pagination against Postgres, because later batches become progressively more expensive.
- Postgres should have composite indexes that match the remaining OLTP-backed usage and refresh filters.
- The frontend should not pull full grouped usage summaries only to slice them client-side.

## Non-goals

- Do not replace the OLTP path for user-owned usage summaries.
- Do not add ClickHouse or a continuously running OLAP service.
- Do not push to GitHub from this task.

## Acceptance Criteria

- Admin usage page refresh calls DuckDB totals, limited summary, limited time buckets, and limited drilldown.
- DuckDB refresh pages through Postgres with `(started_at, request_id)` keyset cursors.
- Alembic has a migration for filter-first request fact indexes.
- Regression tests cover totals and limited summary behavior.
