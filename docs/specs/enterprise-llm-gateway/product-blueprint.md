# Product Blueprint

## Purpose

Define the product outcome, scope, users, non-goals, and phase posture that all gateway contracts inherit.

## Contract Metadata

| Field | Value |
| --- | --- |
| Scope | Broad product blueprint before implementation pruning |
| Owner | Product blueprint |
| Consumers | Architecture, contract specs, TDRs, delivery planning |
| Evidence | Goals, non-goals, personas, workflows, success measures |
| Open decisions | Technology and operations choices stay in TDRs and open-decision register |

## Thesis

The enterprise LLM gateway gives one organization a governable downstream entrance to heterogeneous model-serving paths while preserving the inference efficiency of self-hosted vLLM serving. It should answer two operator questions well:

1. Can users and services access the right models safely through one managed ingress?
2. Is current serving capacity efficient and sufficient, or does the organization need more capacity?

## Product Goals

1. Preserve efficient serving for long-context and repeated-prefix workloads.
2. Govern downstream access by user, service, project, model, route, IP, and rate policy.
3. Support the protocol bridges needed by current clients and provider paths.
4. Attribute pressure and token use by time window, person, project, model category, route, and pool.
5. Give operators a UI that is useful for repeated configuration and capacity decisions.
6. Keep the product open to later business augmentation without turning the gateway core into an unfocused platform.

## Non-goals

- Rebuild vLLM Router prefix/cache-aware endpoint routing inside the gateway.
- Replace an enterprise IAM or secret-vault product.
- Start as a payment, invoice, or full billing system.
- Treat business-value token efficiency as a first hard metric before outcome signals are defined.
- Lock gateway language, UI framework, identity provider, deployment topology, or request-content retention before TDR review.

## Primary Personas

| Persona | Primary Jobs |
| --- | --- |
| Platform operator | Configure pools, aliases, policy, credentials, dashboards, alerts |
| Project owner | Understand project usage, pressure, efficiency, and entitlement |
| Downstream human user | Use one gateway key and approved model aliases |
| Downstream service owner | Operate automation with scoped policy, limits, and attribution |
| Future product owner | Preserve tenant boundaries and extension points |

## Product Layers

| Layer | Product Role |
| --- | --- |
| Downstream ingress | Stable API and key surface visible to clients |
| Policy and identity | Eligibility, limits, IP policy, audit authority |
| Routing and adapter layer | Select eligible routes and convert compatible protocols |
| Serving integration | Providers and vLLM Router pools |
| Analytics and telemetry | Capacity facts, telemetry, audit evidence |
| Operator surface | UI and admin APIs |
| Extension surface | Future knowledge, feedback, and product enrichment |

## Capability Map

### Gateway Core

- Downstream API keys for users and services.
- Model aliases and model categories.
- Project attribution.
- Route policies and health-aware selection.
- Rate limits, access policy, IP allowlists.
- Streaming, cancellation, timeout, retry, and fallback policy.
- Upstream credential references and isolation.

### Serving Integrations

- vLLM Router pools for same-model-type vLLM endpoints.
- vLLM `v1/completions` path.
- Provider or OpenAI-compatible route targets.
- Future route target families behind the same route-target interface.

### Protocol Compatibility

- Legacy OpenAI v1 ingress behavior required by current clients.
- Anthropic conversion only for defined capability subsets.
- Codex-specific Responses bridge only for defined use cases.
- Usage, error, streaming, and unsupported-field semantics.

### Capacity Operations

- Request and token pressure dashboards.
- Time window, person, project, model, route, and pool drilldowns.
- Prompt, completion, cached, and context-efficiency facts when upstream evidence supports them.
- Capacity decision reports and trend comparisons.

### Operator Experience

- Inventory, pool health, model catalog, alias, and route management.
- User/service key, project, policy, and IP allowlist management.
- Usage and pressure views.
- Privileged change audit views.
- Admin APIs for automation of the same core workflows.

## Core Workflows

### Downstream User Call

1. Client authenticates to the gateway.
2. Gateway attaches user/service/project attribution.
3. Policy layer computes eligible model categories and route targets.
4. Adapter layer validates request compatibility.
5. Routing layer selects an eligible pool.
6. Upstream call streams or completes.
7. Gateway normalizes response and usage facts.
8. Analytics, telemetry, and audit hooks persist the correct evidence class.

### Operator Adds A vLLM Route

1. Operator registers or updates one vLLM Router pool.
2. Operator maps a gateway model category or alias to the pool.
3. Operator attaches access and rate policy.
4. Operator validates route health and protocol compatibility.
5. Operator monitors pressure and project attribution after rollout.

### Capacity Decision

1. Operator selects a pressure window.
2. Dashboard ranks pools, models, projects, and users.
3. Operator compares request load, tokens, latency, failures, queue/cache signals, and context efficiency.
4. Operator records whether pressure is policy-driven, project-driven, model-driven, or capacity-driven.

## Success Measures

| Area | Blueprint Success |
| --- | --- |
| Efficiency | Long-context route path preserves vLLM Router locality ownership |
| Governance | Upstream keys stay hidden and downstream identity is attributable |
| Analytics | Pressure is explainable by time, person, project, model, and pool |
| Protocol | Conversion semantics are explicit and testable |
| Operations | Operators can configure and inspect the system without hand-editing every route |
| Extensibility | Future product/business augmentation has a boundary instead of invasive rewrites |

## Phase Posture

- **Blueprint now:** broad contracts and TDRs.
- **First slice later:** a minimal gateway data plane, one vLLM Router path, baseline policy, baseline analytics, and a focused operator console.
- **Enterprise hardening later:** HA, richer adapters, richer telemetry and policy, broader provider coverage, tenant controls.
- **Business augmentation later:** knowledge, feedback, user habit, and outcome signals.

## Acceptance Checks

1. Product goals, non-goals, personas, and success measures are visible before implementation detail.
2. Efficiency, governance, analytics, and future augmentation remain separate traceable product threads.
3. Open technology choices are not silently fixed in the product narrative.
