# Phased Delivery

## Purpose

This file shows how to prune the broad blueprint into delivery slices without rewriting the product model.

## Contract Metadata

| Field | Value |
| --- | --- |
| Scope | Blueprint pruning and phase inputs |
| Owner | Phased delivery |
| Consumers | Future implementation plans, TDR closure, first-slice staffing |
| Evidence | Included/deferred capability lists and success evidence |
| Open decisions | Final first-slice build order waits on TDR and adapter subset decisions |

## Phase Strategy

| Phase | Intent |
| --- | --- |
| Blueprint | Finish contracts, TDRs, and implementation-ready decision inputs |
| First slice | Prove gateway ingress, policy, vLLM route, analytics facts, and operator minimum |
| Enterprise hardening | Broaden adapters, safety, HA, observability, and governance |
| Product expansion | Add commercialization and business augmentation deliberately |

## First Slice Candidate

### Include

- One downstream authentication path for human and service keys.
- Project attribution and basic model entitlement.
- IP allowlist on at least key or policy scope.
- Rate/concurrency baseline.
- Model alias to one vLLM Router/OpenAI-compatible route target; legacy
  `v1/completions` is out of scope.
- One explicit legacy-to-Anthropic adapter subset or a scoped test route.
- Codex-scoped Responses bridge subset if Codex is an early consumer.
- Usage facts in PostgreSQL and online counters in Redis where needed.
- Operator minimum for keys, projects, routes, policy, and pressure view.
- Telemetry correlation across request, policy, adapter, and route attempt.

### Exclude Or Defer

- Gateway-owned vLLM endpoint cache routing.
- Broad arbitrary provider compatibility.
- Complex billing.
- Full business augmentation.
- Multi-region commitments.
- Heavy analytics datastore before measured need.

## First Slice Success Evidence

- A scoped user can reach an allowed vLLM completion route and a blocked user cannot.
- Same route emits request, route attempt, usage, telemetry, and audit evidence in the correct classes.
- Operator can see project pressure by selected window.
- Unsupported adapter feature fails explicitly.
- Basic load test exposes policy/rate overhead and streaming path behavior.

## Enterprise Hardening Slice

Potential additions:

- More policy attachment scopes and delegated project admin.
- Richer route strategies and rollout controls.
- Stronger retention tooling and audit export.
- More protocol matrix rows and provider paths.
- HA topology, restore drills, load budgets, alert tuning.
- Richer capacity reports and rollups.

## Product Expansion Slice

Potential additions:

- Tenant-facing product boundaries.
- Commercial entitlement and billing integrations.
- Knowledge, feedback, and user-habit extensions.
- Business-outcome analytics after governed labels exist.

## Pruning Rules

1. Keep product invariants and ownership matrix intact.
2. Remove whole capability rows from a phase rather than weaken security semantics silently.
3. Preserve explicit unsupported behavior for deferred protocol matrix rows.
4. Preserve storage and language TDR status until a decision gate resolves them.
5. Use implementation plans to choose build order after the blueprint is reviewed.

## Implementation Plan Inputs

Later implementation planning needs:

- Chosen gateway language and service topology.
- Selected first adapter subsets.
- Chosen UI framework.
- PostgreSQL/Redis operational baseline.
- Concrete vLLM Router pool test environment.
- Retention default for content, facts, audit, and telemetry.

## Acceptance Checks

1. The first slice is smaller than the blueprint but does not violate blueprint invariants.
2. Deferred adapter features remain explicit matrix rows.
3. Hardening and product expansion are separated from proof-of-core work.
4. Later implementation planning has named decisions to close before code expansion.
