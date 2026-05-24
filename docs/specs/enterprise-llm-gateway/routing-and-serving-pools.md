# Routing And Serving Pools

## Purpose

This contract defines routing ownership above serving pools. It exists to preserve prefix-local serving efficiency without turning the gateway into a second vLLM Router.

## Contract Metadata

| Field | Value |
| --- | --- |
| Scope | Gateway route selection and vLLM Router pool boundary |
| Owner | Routing and serving pools |
| Consumers | Policy, protocol adapters, telemetry, operator UI, delivery planning |
| Evidence | Route objects, flow, retry/fallback limits, pool integration checks |
| Open decisions | First-slice route policy and affinity-hint integration remain implementation decisions |

## Authoritative Boundary

| Concern | Gateway | vLLM Router Pool |
| --- | --- | --- |
| Downstream model alias resolution | Owns | Does not own |
| User, service, project, and policy eligibility | Owns | Does not own |
| Selection among provider targets and pools | Owns | Does not own |
| Selection among same-model vLLM endpoints | Does not own | Owns |
| Prefix/cache-aware worker-local routing | Does not own | Owns |
| Pool health facts exposed to operators | Consumes and summarizes | Emits or exposes |

The gateway may pass router-relevant hints that are explicitly supported by the pool contract. It may not rebuild endpoint-level prefix trees, cache hit predictors, worker hash rings, or worker-local balancing logic for vLLM pools.

## Routing Objects

| Object | Definition |
| --- | --- |
| Model alias | Stable downstream name requested by clients |
| Model category | Policy-visible family such as a model line, provider class, or self-hosted model class |
| Route target | One callable upstream target with declared protocol and capability contract |
| Serving pool | Route target that represents a pool instead of one provider endpoint |
| vLLM Router pool | Serving pool backed by vLLM Router for same-model-type vLLM endpoints |
| Route policy | Ordered or weighted rule set applied only after policy eligibility |
| Affinity hint | Stable non-secret value supplied to a route target when supported |

## Route Catalog Contract

Each route target needs:

- Stable ID and operator label.
- Owning organization and future tenant scope.
- Target kind: `vllm_router_pool`, `provider`, or later extension kind.
- Protocol capabilities and adapter path.
- Model categories and aliases it can serve.
- Policy tags such as environment, sensitivity, region, or project class.
- Health source and activation state.
- Upstream credential reference, never secret material in route config returned to downstream callers.
- Optional fallback relationship and retry budget.

For vLLM Router pools, the catalog also records:

- Served model type and version label.
- Router base URL and gateway-facing auth reference.
- Router policy posture known to operators, such as a cache-locality-oriented pool versus a general pool.
- Optional affinity-hint mapping that is supported by the router integration.
- Router metric and health scrape linkage when available.

## Request Routing Flow

1. Resolve the downstream model alias to one or more model categories.
2. Evaluate caller identity, project attribution, IP policy, model entitlement, and rate policy.
3. Ask the protocol adapter whether each policy-eligible route can serve the request subset.
4. Filter unhealthy, disabled, draining, or incompatible route targets.
5. Apply route policy among remaining candidates.
6. Call the selected route target and preserve cancellation and streaming backpressure.
7. Emit selected-route facts and explicit failure facts.

No step after policy evaluation may re-add a route target removed by eligibility.

## Selection Policy Families

The blueprint allows these above-pool policies:

| Policy Family | Intended Use |
| --- | --- |
| Ordered preference | Prefer self-hosted or approved path, fall back only when allowed |
| Weighted split | Rollout, canary, or controlled provider distribution |
| Health constrained | Avoid disabled, unhealthy, draining, or incompatible targets |
| Project constrained | Restrict project classes to approved pools |
| Capacity guard | Optionally avoid targets under operator-defined pressure threshold |

The first implementation slice can start with ordered preference plus health constraints. Cache-aware endpoint selection remains inside vLLM Router.

## vLLM Router Pool Integration

### Required Behavior

- One gateway route target represents a router-backed pool for the same model type.
- The gateway forwards only supported OpenAI-compatible request shapes for the pool route.
- The pool owns endpoint-local balancing and prefix-locality behavior.
- The gateway correlates its request ID with router/upstream evidence when the integration supports it.

### Affinity Posture

Repeated long-context conversations should preserve serving locality when the chosen router policy supports it. The gateway may derive a stable affinity hint from an approved context such as conversation ID, session ID, or scoped project/user key. The hint contract must:

- Avoid raw prompt content.
- Avoid upstream secret material.
- State whether the hint is stable across retries.
- State trust-group implications for any cache-isolation salt or equivalent feature.

### Pool Segmentation

Operators may choose separate pools when serving assumptions differ materially:

- Different model type or tokenizer behavior.
- Different trust or cache-isolation group.
- Different SLA or project class.
- Different hardware or rollout channel that should not share a route policy.

## Retry And Fallback Routing

| Situation | Contract |
| --- | --- |
| Request rejected by identity or IP policy | No upstream attempt |
| Route incompatible with protocol subset | No silent downgrade; choose another compatible eligible target or fail |
| Upstream timeout before response body | Retry only if route policy and idempotency posture permit |
| Mid-stream failure | Emit partial failure; do not fabricate a complete response |
| Fallback | Re-run route candidate filtering inside existing eligibility constraints |

Long prompts make replay expensive. Retry budgets must be finite and visible in capacity facts.

## Route Evidence

Every attempted request should produce a route fact with:

- Gateway request ID.
- Caller and project attribution IDs.
- Requested alias and selected model category.
- Candidate route family decision outcome.
- Selected target and pool ID when selected.
- Protocol adapter path.
- Attempt number, fallback marker, start/end timing, outcome, and failure class.

Pool-local endpoint identity is optional gateway evidence. It should be captured only when the router exposes it safely and operators need it.

## Operator Workflows

- Register, validate, activate, drain, and disable a pool.
- Map aliases and model categories to route targets.
- Inspect selected-route distribution and fallback behavior.
- Compare pressure across same-model pools and provider alternatives.
- See pool capability mismatches before activation.

## Non-goals

- Gateway-side implementation of vLLM Router endpoint policies.
- Cross-pool promise that prefix caches are reusable.
- Hidden fallback from self-hosted route to external provider without policy and audit visibility.

## Acceptance Checks

1. A supported `v1/completions` request can be routed from one alias to one eligible vLLM Router pool.
2. A disabled or policy-ineligible route cannot be selected by fallback.
3. The route contract names affinity hints without storing prompt content as the key.
4. The spec never describes worker-level prefix/cache endpoint routing as a gateway duty.
