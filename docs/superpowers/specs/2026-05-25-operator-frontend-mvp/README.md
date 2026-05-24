# Operator Frontend MVP Spec Group

## Purpose

This spec group defines the first operator frontend that can be built against the backend currently present in this repository. It intentionally avoids screens that require unavailable backend capabilities such as SSO, vLLM Router process supervision, pagination, billing, or time-bucketed analytics.

## Source Backend

The current backend exposes:

- Gateway proxy routes: `/v1/chat/completions`, `/v1/messages`, `/v1/models`.
- Health routes: `/health/live`, `/health/ready`, `/admin/diagnostics`.
- Admin resources: subjects, projects, project memberships, gateway keys, model aliases, model entitlements, upstreams, router command configs, rate policies, usage summary, and audit events.

## Artifacts

| File | Role |
| --- | --- |
| `backend-capability-map.md` | Exact backend API and frontend affordance map |
| `information-architecture.md` | Routes, navigation, and page ownership |
| `screen-specs.md` | Detailed screen requirements and actions |
| `api-client-and-state.md` | Frontend data client, auth token handling, state, validation |
| `testing-and-acceptance.md` | Frontend verification matrix |
| `../../plans/2026-05-25-operator-frontend-mvp.md` | Implementation task plan |

## MVP Frontend Goal

Build a dense operator console that lets an internal operator:

1. Verify gateway readiness and LiteLLM version.
2. Create and manage subjects, projects, project memberships, and gateway keys.
3. Configure model aliases, per-model IP allowlists, entitlements, upstreams, and upstream health checks.
4. Configure rate policies and vLLM Router command configs.
5. Inspect usage summary and recent audit events.

## Non-Goals

- No login/SSO workflow.
- No public user-facing chat playground.
- No prompt or response body viewer.
- No vLLM process start/stop/restart.
- No billing or budget enforcement UI.
- No client-side attempt to reveal or reconstruct secret values.
- No frontend-only analytics claims that the backend does not expose.

## Design Contract

Use `DESIGN.md` as the visual and UX source of truth. The console is operational software: compact tables, precise forms, semantic badges, redacted secrets, and strong empty/error states.
