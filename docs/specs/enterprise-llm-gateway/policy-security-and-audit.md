# Policy, Security, And Audit

## Purpose

This is the authoritative contract for request eligibility, IP restrictions, privileged changes, and credential isolation. Routing consumes these decisions; it does not override them.

## Contract Metadata

| Field | Value |
| --- | --- |
| Scope | Identity, eligibility, IP allowlists, rate posture, credential isolation, audit |
| Owner | Policy, security, and audit |
| Consumers | Routing, control plane, storage, telemetry, analytics, deployment |
| Evidence | Evaluation order, denial semantics, fallback limits, audit event fields |
| Open decisions | IdP timing, trusted ingress extraction, and exact rate algorithms remain open |

## Security Goals

1. Keep upstream credentials invisible to downstream users and normal operator views.
2. Attribute every accepted request to a gateway subject and project scope.
3. Fail closed for identity, IP allowlist, model entitlement, and secret resolution.
4. Make privileged changes and security-significant denials reviewable.
5. Preserve organization and tenant boundaries before external commercialization exists.

## Identity Model

| Subject | Example | Notes |
| --- | --- | --- |
| Human user | Engineer using a gateway key | May belong to projects and operator roles |
| Service account | CI agent or application service | Must have scoped keys and limits |
| Operator | Platform admin action | Uses control-plane auth and audit |
| Future tenant actor | External customer user or service | Boundary is reserved in entities and policies |

The gateway may begin with gateway-managed API keys. Later IdP integration must map into the same subject model instead of bypassing policy.

## Resource Model

- Organization and future tenant scope.
- Project.
- Gateway key.
- Model alias and model category.
- Route target and serving pool.
- Policy set and rate policy.
- Upstream credential reference.
- Audit event.

## Policy Inputs

| Input Class | Examples |
| --- | --- |
| Identity | Subject ID, subject type, key status, roles |
| Attribution | Project ID, organization scope, future tenant scope |
| Request | Alias, protocol family, streaming flag, token estimate when available |
| Network | Source IP and trusted proxy chain result |
| Route metadata | Model category, route tags, target kind, health posture |
| Time | Window, activation revision, expiry |

## Authoritative Evaluation Order

1. Parse request envelope and trusted network context.
2. Authenticate the gateway credential or control-plane identity.
3. Resolve organization, future tenant, subject, and project attribution.
4. Enforce key status, subject status, and project status.
5. Enforce IP policy.
6. Enforce alias/model/category entitlement.
7. Enforce request-size, concurrency, and rate policies.
8. Produce eligible route constraints for the routing layer.
9. Record policy decision evidence.

Adapters and routing may add compatibility or health filtering after this order. They may not weaken it.

## Access Rules

Policy should be expressible for:

- Subject or subject group.
- Project.
- Model alias or model category.
- Route target tags or pool class.
- Protocol path or adapter family.
- Source IP allowlist.
- Time-bound exceptions.

The first slice may compile these into a simpler policy table, but the product model should not assume that every key has global access.

## IP Allowlist Contract

### Required Semantics

- IP allowlists can scope a gateway key, subject, project, route policy, or model entitlement.
- The trusted client IP extraction rule is configuration, not ad hoc header parsing.
- Private proxy headers are accepted only from trusted ingress hops.
- A public upstream key is not a reason to expose an unrestricted gateway route.
- Denial happens before upstream request construction.

### Failure Semantics

| Case | Result |
| --- | --- |
| Missing trusted client IP | Fail by configured network policy |
| Source IP not allowed | Deny and audit security-significant evidence |
| Malformed allowlist rule | Reject config activation |
| Route fallback after IP denial | Not allowed |

## Rate And Quota Policy

The blueprint distinguishes:

| Control | Examples |
| --- | --- |
| Request rate | Requests per minute or window |
| Token budget | Prompt, completion, total token estimates or accounted totals |
| Concurrency | Active streams or active upstream attempts |
| Burst behavior | Short-term token bucket or queue policy |
| Administrative quota | Project or subject allowance for reporting and enforcement |

Redis is a likely low-latency coordination store for online counters. PostgreSQL remains the durable configuration and evidence home unless a later TDR changes that posture.

## Credential Isolation

- Downstream keys authenticate only to the gateway.
- Upstream provider keys and router credentials live behind secret references.
- Request logs, audit events, UI views, traces, and analytics facts must not expose secret values.
- Secret rotation changes references or resolved versions without requiring downstream users to learn upstream credentials.
- Adapter errors must redact upstream authorization material before surfacing to clients.

## Policy And Fallback

Fallback may not:

- Cross from a permitted route into a forbidden model category.
- Escape project or IP scope.
- Change provider sensitivity class when policy forbids it.
- Hide that a fallback occurred.

Fallback may:

- Choose another compatible route target already inside eligibility.
- Emit policy and capacity facts that explain the extra attempt.

## Audit Evidence

### Audit Event Classes

| Event Class | Examples |
| --- | --- |
| Privileged configuration | Create, update, activate, disable policy, route, pool, alias |
| Credential lifecycle | Create key, revoke key, rotate secret reference |
| Policy security | IP denial, forbidden model request, repeated auth failure threshold |
| Operator access | Sensitive audit export or elevated control-plane action |
| Retention control | Content sampling or retention configuration change |

### Minimum Audit Fields

- Event ID, timestamp, actor, actor type, organization scope, future tenant scope.
- Action, resource type, resource ID, outcome.
- Request or control-plane correlation ID.
- Policy/config revision before and after when applicable.
- Redacted reason detail.

Raw secrets and prompt/response bodies are not audit fields.

## Evidence Separation

| Evidence | Primary Use | Owner |
| --- | --- | --- |
| Audit event | Accountability and privileged review | This spec |
| Usage fact | Capacity and attribution | Capacity analytics |
| Telemetry | Runtime diagnosis and alerting | Operational telemetry |

One physical write path can emit more than one evidence class, but consumers must know which contract they depend on.

## Control-Plane Safety

- Configuration activation validates policy syntax, target capabilities, IP rule syntax, and secret reference presence.
- Destructive actions such as revoke, disable, or delete have explicit actor audit.
- Draft or staged config should be distinguishable from active config.
- Operator UI and admin APIs must use the same core command semantics.

## Tenant-Ready Boundary

The first organization can be implicit in UI defaults. It must not be erased from:

- Policy keys.
- Resource IDs or unique constraints.
- Audit events.
- Analytics attribution.
- Secret-reference ownership.

## Acceptance Checks

1. IP-denied requests never construct an upstream call.
2. Fallback cannot recover a route that policy removed.
3. Upstream credential material is absent from downstream responses, normal UI tables, analytics facts, audit events, and traces.
4. Privileged policy and route changes produce auditable actor-linked events.
5. A service account can be scoped more narrowly than an organization-wide key.
