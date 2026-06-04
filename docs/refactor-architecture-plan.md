# LLM Gateway Architecture Refactor Plan

## Goal

Keep the gateway lightweight while separating the codebase by responsibility:

- API controllers own request parsing, dependency injection, HTTP status, and response shape.
- Services own reusable business behavior, serialization helpers, protocol adaptation, accounting, analytics, and runtime metrics.
- Database models remain persistence declarations, not business workflow containers.
- Frontend state, labels, API types, and reusable widgets should move out of the single route file before new UI features are added.

## Current Hotspots

- `frontend/src/routes/+page.svelte`: single-page admin/user console with mixed navigation, forms, filtering, labels, and data fetching.
- `src/llm_gateway/api/admin.py`: broad CRUD controller with many resource-specific operations.
- `src/llm_gateway/api/proxy.py`: critical data-plane route file; must preserve short DB sessions before upstream calls.
- `src/llm_gateway/api/auth.py`: mixed self-service auth, managed-resource operations, and payload formatting.

## Refactor Boundaries

### Data Plane

Do not reintroduce request-scoped DB sessions in proxy routes. The safe shape is:

1. Open a short SQLAlchemy session.
2. Authenticate gateway key.
3. Resolve policy, route, and rate limits.
4. Detach ORM objects and `rollback()` the implicit transaction.
5. Call upstream model with no DB session held.
6. Record request facts asynchronously.

### Control Plane

Admin and auth APIs can keep request-scoped sessions because they perform bounded CRUD and commit inside the route.

### Analytics

Heavy usage queries stay behind the DuckDB analytics service. Controllers should not embed SQL aggregates for new analytics features unless they are narrow transactional checks.

## Implemented In This Pass

- Moved proxy request-fact construction to `services/proxy_accounting.py`.
- Moved runtime connection tracking into `services/runtime_metrics.py`.
- Moved managed membership role/payload helpers to `services/managed_memberships.py`.
- Moved resource redaction, patch, and pagination helpers to `services/resource_payloads.py`.

## Next Refactor Stages

1. Split `admin.py` by resource group:
   - `api/admin_subjects.py`
   - `api/admin_projects.py`
   - `api/admin_models.py`
   - `api/admin_usage.py`
   - Keep a compatibility router that includes the same prefixes.
2. Split `auth.py` into:
   - self-service auth/session/key endpoints
   - managed project/team endpoints
   - user usage/tutorial endpoints
3. Split frontend route state:
   - extract labels and option builders
   - extract admin resource forms into components
   - extract user dashboard and tutorial panels
4. Add focused tests for each extracted service before moving route files further.

## Non-Goals

- No protocol behavior changes.
- No database schema changes for this refactor.
- No frontend redesign in this pass.
- No change to vLLM router ownership of prefix/prefill locality.
