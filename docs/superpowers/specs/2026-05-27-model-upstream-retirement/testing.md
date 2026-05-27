# Test Spec: Model Alias And Upstream Retirement

## Backend Tests

1. Create a model alias, upstream, and request fact referencing the upstream.
2. Delete the upstream.
3. Assert the upstream row is gone.
4. Assert the request fact remains and `upstream_target_id` is null.
5. Create a model alias with a used upstream.
6. Delete the model alias without cascade and assert structured `409`.
7. Delete the same model alias with `cascade_upstreams=true`.
8. Assert model alias/upstream rows are gone and request fact remains.
9. Patch an upstream Base URL and health path, then assert returned redacted
   upstream reflects the new values.

## Frontend Checks

- Svelte typecheck succeeds.
- Upstream table exposes edit actions for Base URL, health path, name, API key ref,
  API key value, and extra headers.

## Regression Risks

- Accidentally deleting request facts would break audit and analytics.
- Removing the initial no-cascade `409` would remove the UI confirmation guard.
- Leaving the FK attached before upstream deletion can still fail in PostgreSQL.
