# Operator UI And Admin APIs

## Purpose

The operator surface turns the gateway from a proxy into a manageable platform. UI and admin APIs should expose the same command model so automation and humans do not create two control planes.

## Contract Metadata

| Field | Value |
| --- | --- |
| Scope | Operator workflows, UI views, control-plane commands, query expectations |
| Owner | Operator UI and admin APIs |
| Consumers | Policy, routing, analytics, telemetry, storage, UI TDR |
| Evidence | Jobs, screens, commands, validation, audit and capacity UX checks |
| Open decisions | UI framework and final admin transport shape remain open |

## Operator Jobs

| Job | Needed Outcome |
| --- | --- |
| Inventory | Know which aliases, categories, routes, pools, keys, projects, and policies exist |
| Rollout | Validate, activate, drain, disable, and inspect route or policy revisions |
| Access control | Scope users, services, keys, IP allowlists, limits, and model entitlements |
| Capacity review | Inspect pressure by time, project, actor, model, route, and pool |
| Incident review | Correlate failures, fallbacks, denials, and upstream health |
| Audit review | See actor-linked privileged changes and security-significant events |

## UI Product Shape

This is an operator console, not a marketing surface. It should favor dense readable tables, drilldowns, diffs, and filters over decorative dashboards.

## Primary Views

| View | Core Contents |
| --- | --- |
| Overview | Current route health, pressure summary, denials, failure classes |
| Model catalog | Aliases, categories, upstream mappings, protocol capability badges |
| Route and pool inventory | Targets, pools, activation state, health, fallback posture |
| Policy workspace | Access policy, rate policy, IP allowlists, validation, revisions |
| Keys and subjects | Human/service keys, scopes, revocation, recent activity |
| Projects | Ownership, entitlements, usage pressure, efficiency drilldown |
| Capacity analytics | Time-window reports and ranked drivers |
| Telemetry drillthrough | Request correlation and route attempt diagnostics |
| Audit | Privileged events, filters, before/after revision references |
| Settings | Retention, integrations, secret-reference metadata, environment posture |

## Control-Plane Commands

| Resource | Example Commands |
| --- | --- |
| Subject | Create, disable, assign project, assign role |
| Gateway key | Issue, rotate, revoke, scope |
| Project | Create, assign owner, set entitlement |
| Alias/category | Create, map, activate, deprecate |
| Route target | Register, validate, activate, drain, disable |
| Policy | Draft, validate, activate, rollback reference |
| IP allowlist | Create, validate, attach, expire |
| Rate policy | Create, attach, revise |
| Audit export | Query and export through policy |

The API shape may later be REST, RPC, or a mixed command/query surface. The command semantics above are the stable product requirement.

## Admin API Contract Direction

### Command Expectations

- Commands are authenticated and authorized as control-plane actions.
- Mutations emit audit events with actor and config revision.
- Validation endpoints do not activate invalid config.
- Idempotency posture is specified for automation-sensitive commands.
- Secret material is accepted only on secret-management paths and never echoed.

### Query Expectations

- Inventory queries support filtering, sorting, and pagination.
- Analytics queries support bounded time windows and dimension filters.
- Telemetry drillthrough is correlated by request ID or incident filter.
- Audit queries redact sensitive fields and preserve actor/resource identity.

## Revision And Diff Experience

Operators need to know:

- Draft versus active revision.
- What changes before activation.
- Which resources depend on a route or policy.
- Which revision served a historical request when evidence exists.
- Which rollback target is available.

## Capacity UX Requirements

- Overview and drilldown views use the same fact definitions from `capacity-analytics`.
- Missing cache/reuse evidence is visible instead of shown as zero.
- Retry and fallback pressure are visible.
- Selected windows can compare against a prior window.
- Project and actor rankings remain filterable by model and pool.

## Policy UX Requirements

- IP allowlists show attachment scope and expiry.
- Model entitlement and route-policy scope are distinguishable.
- Policy validation warns about unreachable routes or incompatible adapters.
- Destructive actions such as key revoke or pool disable show effect preview where feasible.

## Audit UX Requirements

- Actor, action, resource, outcome, timestamp, and revision are first-class columns.
- Sensitive event details are redacted but not erased into useless text.
- Audit export is itself subject to policy and audit.

## Separation Rules

- UI never renders upstream secret values.
- UI does not modify vLLM Router endpoint membership by bypassing its chosen operational surface unless a later integration spec explicitly owns that.
- Analytics charts do not replace detailed telemetry diagnostics.

## Non-goals

- Expose upstream secret values in normal operator workflows.
- Replace vLLM Router operations with undocumented endpoint mutations.
- Make dashboards the authoritative audit ledger.

## Acceptance Checks

1. Human operators and automation can activate the same policy semantics through one command model.
2. A route validation result shows protocol and secret-reference readiness before activation.
3. Project capacity drilldown can show requests, tokens, retry/fallback pressure, and missing-evidence markers.
4. Audit views expose privileged change history without exposing secret material.
