# Query Optimization Test Spec

## Targeted Backend Tests

- `tests/test_duckdb_analytics.py` verifies DuckDB refresh, totals, limited summary, time bucket limit, drilldown limit, and ranking.

## Frontend Checks

- `npm run check` verifies Svelte and TypeScript after adding `usageTotals`.
- `npm run build` verifies the optimized admin page compiles.

## Full Regression

- `uv run pytest -q`
- `uvx ty check`
- `uvx ruff check .`
- `uvx ruff format --check .`
- `npm run test`
- `npm run test:e2e`

## Manual Risk Notes

- The admin usage page now reads total banner metrics from `/admin/usage/duckdb/totals`, so it can keep the visible detail table capped without losing full-range totals.
- The migration is applied automatically by the existing startup database initialization path.
