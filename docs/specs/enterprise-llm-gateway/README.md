# Enterprise LLM Gateway Specification Group

## Purpose

This directory is the broad product blueprint for the enterprise LLM gateway. It is intentionally wider than the first implementation slice. Later delivery plans should prune from these contracts instead of replacing the product model.

## Current Status

- Product stage: blueprint
- Repository stage: greenfield
- Primary driver: inference efficiency and capacity evidence
- Highest serving invariant: the gateway does not reimplement vLLM Router prefix/cache-aware endpoint routing
- Decision posture: major technology choices stay reviewable through TDRs

## Source Artifacts

- Interview requirements: `../../../.omx/specs/deep-interview-enterprise-llm-gateway-spec.md`
- Approved consensus plan: `../../../.omx/plans/master-enterprise-llm-gateway-spec-group.md`
- Plan acceptance matrix: `../../../.omx/plans/test-spec-enterprise-llm-gateway-blueprint.md`

## Artifact Index

| Artifact | Role |
| --- | --- |
| [product-blueprint.md](product-blueprint.md) | Goals, personas, scope, non-goals, phase model |
| [system-architecture.md](system-architecture.md) | Runtime layers, trust zones, invariants, flows |
| [routing-and-serving-pools.md](routing-and-serving-pools.md) | Gateway routing and vLLM Router pool boundary |
| [policy-security-and-audit.md](policy-security-and-audit.md) | Policy order, identity, IP allowlists, audit authority |
| [protocol-compatibility.md](protocol-compatibility.md) | Protocol matrices and conversion rules |
| [capacity-analytics.md](capacity-analytics.md) | Pressure attribution and token-efficiency analytics |
| [operational-telemetry.md](operational-telemetry.md) | Metrics, logs, traces, alerts, health handoff |
| [operator-ui-and-admin-apis.md](operator-ui-and-admin-apis.md) | Operator workflows and admin resources |
| [data-model-and-storage.md](data-model-and-storage.md) | Entity model, storage posture, retention |
| [deployment-operations-and-safety.md](deployment-operations-and-safety.md) | Deployment, failure operations, migrations, recovery |
| [extension-and-business-augmentation.md](extension-and-business-augmentation.md) | Future knowledge and feedback boundaries |
| [phased-delivery.md](phased-delivery.md) | Blueprint pruning into delivery slices |
| [testing-and-acceptance.md](testing-and-acceptance.md) | Cross-spec verification scenarios |
| [verification-audit.md](verification-audit.md) | Requirement-by-requirement blueprint evidence audit |
| [tdrs/gateway-language-options.md](tdrs/gateway-language-options.md) | Go, C#, Rust decision framing |
| [tdrs/storage-and-analytics-posture.md](tdrs/storage-and-analytics-posture.md) | PostgreSQL, Redis, analytics escalation framing |
| [tdrs/operator-ui-framework-options.md](tdrs/operator-ui-framework-options.md) | SolidJS and Svelte decision framing |
| [tdrs/open-decisions.md](tdrs/open-decisions.md) | Deferred architecture and product decisions |

## Product Invariants

1. Downstream users call the gateway, not upstream providers or serving pools directly.
2. Upstream credentials are never exposed to downstream users.
3. Gateway policy eligibility is decided before route selection and cannot be bypassed by retries or fallback.
4. vLLM Router pools choose same-model serving endpoints and serving-locality behavior inside the pool.
5. The gateway chooses the model category, provider path, and serving pool path above that pool boundary.
6. Capacity analytics and operational telemetry are adjacent but not the same contract.
7. Protocol conversion must state what maps, what degrades, and what is rejected.

## Glossary

| Term | Meaning |
| --- | --- |
| Gateway | The downstream ingress, policy, protocol, analytics, and operator layer defined by this blueprint |
| Downstream client | A user or service caller authenticated by the gateway |
| Upstream | Any provider, vLLM Router pool, or serving service behind the gateway |
| Model category | A gateway-visible family or class used for policy and route selection |
| Model alias | A stable downstream name resolved to one model category or route policy |
| Serving pool | A gateway route target representing one provider target or one router-backed cluster |
| vLLM Router pool | Same-model-type vLLM endpoints aggregated behind vLLM Router |
| Route eligibility | Policy decision that a caller may use a model category and candidate pool |
| Route selection | Choosing among eligible route targets using route policy and health facts |
| Project | Usage attribution scope owned by the organization and attached to callers or requests |
| Pressure fact | A measured request, token, latency, queue, cache, or utilization signal used for capacity analysis |
| Context efficiency | How well a project reuses context and avoids wasteful repeated long prefixes |
| Tenant-ready | Internal-first design that preserves organization boundaries for future external tenancy |

## Ownership Matrix

| Concern | Authoritative Artifact | Dependent Artifacts |
| --- | --- | --- |
| Product principles | `product-blueprint` | All |
| Gateway and vLLM Router split | `system-architecture` | Routing, telemetry, deployment |
| Route eligibility | `policy-security-and-audit` | Routing, UI, APIs |
| Route selection | `routing-and-serving-pools` | Policy, telemetry |
| Fallback constraints | `policy-security-and-audit` | Routing, protocol |
| Protocol matrices | `protocol-compatibility` | Adapters, testing |
| Capacity facts | `capacity-analytics` | UI, data model |
| Runtime telemetry | `operational-telemetry` | Deployment, analytics |
| Privileged audit events | `policy-security-and-audit` | UI, data model |
| Entity model and storage posture | `data-model-and-storage` | Policy, analytics, APIs |
| Future business augmentation | `extension-and-business-augmentation` | Product, data model |

## Requirement Traceability

| Requirement | Blueprint Coverage | Verification |
| --- | --- | --- |
| Prefix-aware efficiency | Architecture and routing delegate serving-locality routing to vLLM Router pools | Routing invariant scenario |
| User/token/request audit | Capacity analytics facts and dashboards | Analytics matrix |
| vLLM Router route target balancing | Routing and protocol specs | vLLM route target scenario |
| OpenAI legacy to Anthropic | Protocol compatibility matrix | Conversion matrix review |
| Codex path to Responses | Protocol compatibility matrix | Codex bridge scenario |
| IP whitelist | Policy/security policy order | Negative policy bypass scenario |
| Internal-first, future commercialization | Product, policy, data model | Tenant-ready boundary check |
| Lean stack bias | Storage and UI TDRs | TDR unresolved-decision scan |

## References

- vLLM Router load balancing strategies: <https://github.com/vllm-project/router/blob/main/docs/load_balancing/README.md>
- vLLM automatic prefix caching: <https://docs.vllm.ai/en/latest/design/prefix_caching.html>
- vLLM metrics: <https://docs.vllm.ai/en/latest/design/metrics.html>
- vLLM OpenAI-compatible server: <https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html>
- OpenAI Responses migration guidance: <https://platform.openai.com/docs/guides/migrate-to-responses>
- Anthropic OpenAI SDK compatibility note: <https://docs.anthropic.com/en/api/openai-sdk>
