# Testing Plan

## Frontend

- Run `npm run check` to validate Svelte and TypeScript.
- Run `npm run build` to catch SSR/client compilation issues.
- Run `npm run test` and `npm run test:e2e` for existing smoke coverage.
- Add or preserve UI helper logic in a type-safe way; no unbounded table rendering
  should remain on the named pages.

## Backend

- Extend DuckDB analytics query tests to cover the optional limit on time buckets
  and drilldown.
- Run targeted DuckDB analytics tests and the full pytest suite.

## Manual Review

- Inspect the changed Svelte template for shared filter state leaks.
- Confirm old overview content is represented on the usage page before removing
  the old overview route.
