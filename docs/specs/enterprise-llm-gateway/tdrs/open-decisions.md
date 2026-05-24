# Open Decisions

## Purpose

This register keeps unresolved decisions visible so domain specs do not quietly settle them.

## Decision Register

| Decision | Why It Matters | Needed Evidence |
| --- | --- | --- |
| Gateway language | Data-plane maintainability and performance | Benchmark, team fluency, library shortlist |
| Control/data-plane topology | Scaling, rollout, failure isolation | First-slice service boundaries and deployment target |
| UI framework | Operator delivery speed | Representative screen spikes |
| IdP integration timing | Identity model and enterprise operations | Internal auth needs and org standard |
| Trusted ingress/IP extraction | IP allowlist correctness | Deployment topology and proxy chain |
| Retention defaults | Privacy, analytics, compliance cost | Capacity questions and security posture |
| Prompt/response sampling | Debug value versus sensitivity | Explicit security and product decision |
| PostgreSQL HA and Redis topology | Recovery and availability | Production environment needs |
| External commercialization threshold | Tenant and billing work timing | Product commitment and customer path |
| Anthropic adapter first subset | Client compatibility | Real request corpus and required feature rows |
| Codex Responses subset | Codex compatibility | Codex request corpus and upstream expectations |
| vLLM Router affinity hints | Long-context locality and isolation | Router integration test and trust-group policy |

## Decisions Already Fixed By Blueprint

- Gateway remains the only downstream ingress.
- Gateway does not implement same-model vLLM endpoint prefix/cache routing.
- Route eligibility is policy-owned before route selection.
- Storage starting bias is PostgreSQL plus Redis unless evidence changes it.
- Capacity analytics and operational telemetry are separate contracts.

## Closure Rule

Close a decision by updating its TDR or implementation plan with:

1. Chosen option.
2. Evidence used.
3. Rejected alternatives.
4. Invalidation trigger.
5. Verification impact.
