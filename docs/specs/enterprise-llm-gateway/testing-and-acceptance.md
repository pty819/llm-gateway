# Testing And Acceptance

## Purpose

This file is the blueprint verification contract. It does not prescribe code tests yet. It states what later implementation plans must prove and what the spec group itself must make unambiguous.

## Contract Metadata

| Field | Value |
| --- | --- |
| Scope | Blueprint verification matrix and later implementation test families |
| Owner | Testing and acceptance |
| Consumers | All spec contracts, phased delivery, future implementation plans |
| Evidence | Completion gates, positive/negative scenarios, review checklists |
| Open decisions | Concrete implementation harnesses follow selected stack and first-slice decisions |

## Spec Group Completion Gate

The blueprint is reviewable when:

1. Every artifact in the index exists.
2. The ownership matrix has no duplicate authoritative owner for critical concerns.
3. Product requirements map to specs and acceptance checks.
4. Protocol specs contain compatibility matrices.
5. TDRs expose unresolved decisions instead of hiding them in contract specs.

## Positive Scenarios

### vLLM Completion Path

- Given a gateway key with project attribution and eligible model policy
- When a downstream client sends a supported legacy completion request
- Then the gateway evaluates policy, selects a vLLM Router pool, passes supported request semantics, normalizes outcome and usage facts, and emits telemetry.

### Codex Responses Bridge

- Given a Codex-marked route and an approved compatibility subset
- When the gateway receives the supported legacy-facing request shape
- Then it maps only approved semantics to Responses and reports unsupported semantics explicitly.

### Operator Capacity Decision

- Given usage facts over a selected pressure window
- When an operator inspects project, user, model, and pool drilldowns
- Then the evidence separates request load, token pressure, latency, failure, and context-efficiency signals.

### Policy-Controlled Failure

- Given a failed upstream target and configured fallback
- When the gateway considers fallback
- Then fallback only selects another eligible target and emits failure evidence.

## Negative Scenarios

| Case | Required Result |
| --- | --- |
| Upstream credential reaches downstream response or UI | Prohibited by trust boundary |
| Unsupported protocol field is silently ignored without matrix rule | Spec failure |
| Fallback escapes access, IP, project, or model policy | Spec failure |
| Internal-first entity model removes future tenant boundary | Spec failure |
| Gateway duplicates vLLM prefix/cache endpoint algorithm | Spec failure |

## Implementation Verification Families

| Family | Examples |
| --- | --- |
| Unit | Policy evaluation, adapter matrix rows, route candidate filtering, usage normalization |
| Integration | Gateway to vLLM Router pool, gateway to provider adapter, facts to storage |
| End-to-end | User request, operator rollout, pressure dashboard, fallback incident |
| Load and performance | Streaming concurrency, long-prompt behavior, rate-limit behavior, analytics write pressure |
| Security | Credential isolation, IP allowlist, policy bypass, secret redaction |
| Observability | Metrics, trace linkage, audit events, capacity dashboard facts |

## Traceability Checklist

- Efficiency requirement is linked to routing and telemetry evidence.
- Permission requirement is linked to policy and UI evidence.
- Usage audit requirement is linked to capacity facts and retention posture.
- Protocol bridge requirement is linked to compatibility matrices.
- Tech-stack preference is linked to TDR review gates.

## Spec Review Checklist

- Terminology matches the glossary.
- Same field is not named differently across protocol, data, and UI specs.
- Same metric is not defined differently in analytics and telemetry specs.
- Policy evaluation order appears once as the authoritative contract.
- TDR biases are not copied into contract specs as settled decisions.

## Non-goals

- Claim implementation correctness before executable code exists.
- Replace protocol matrices, policy order, or capacity fact definitions owned by other specs.

## Acceptance Checks

1. Completion gates, positive cases, negative cases, and verification families are all explicit.
2. The review checklist points back to authoritative cross-spec ownership.
3. Later implementation plans can derive code-level tests without inventing blueprint semantics.
