# Capacity Analytics

## Purpose

Capacity analytics answers whether model-serving pressure is efficient, attributable, and sufficient for planning more capacity. It is not the live alerting contract and it is not a billing ledger.

## Contract Metadata

| Field | Value |
| --- | --- |
| Scope | Capacity facts, attribution, dimensions, measures, and dashboard questions |
| Owner | Capacity analytics |
| Consumers | Operator UI, storage, delivery planning, future business augmentation |
| Evidence | Fact grain, derived indicators, drilldowns, privacy and retention posture |
| Open decisions | Retention periods and heavier analytics escalation remain open |

## First Questions To Answer

1. Which time windows, users, services, projects, models, and pools create the most inference pressure?
2. Are requests expensive because of prompt load, completion load, retries, fallbacks, long latency, or poor repeated-context reuse?
3. Which projects use tokens efficiently enough to justify current capacity posture?
4. When pressure rises, is the driver demand growth, route behavior, model mix, policy behavior, or serving saturation?

## Evidence Classes

| Evidence | Use In Analytics |
| --- | --- |
| Gateway usage fact | Primary attribution fact |
| Route attempt fact | Selected target, retry, fallback, failure class |
| Normalized usage fact | Prompt, completion, total, cache/reuse marker |
| Aggregated vLLM pool signal | Context for cache, queue, token, and latency pressure |
| Audit event | Join sparingly for privileged configuration context |
| Telemetry trace | Drill-through diagnostic reference, not analytics authority |

## Fact Grain

The default durable grain is one logical gateway request plus zero or more route attempts.

### Logical Request Fact

- Request ID, start/end time, outcome.
- Subject, subject type, service marker, project, organization, future tenant.
- Requested alias, resolved model category, downstream protocol family.
- Streaming marker and accepted request size class.
- Policy decision class.
- Final route target and pool when any.

### Attempt Fact

- Attempt ID and parent request ID.
- Route target, pool, provider kind, adapter path.
- Retry number and fallback reason when present.
- Upstream timing slices when available.
- Failure class and partial-stream marker.

### Usage Fact

- Prompt/input tokens.
- Completion/output tokens.
- Total tokens.
- Cached/reused token facts when available.
- Estimated marker and source.
- Latency facts useful for pressure analysis.

## Dimensions

| Dimension | Minimum Drilldown |
| --- | --- |
| Time | Minute, hour, day, comparison window |
| Actor | Human user, service account, subject group |
| Project | Project and project owner grouping |
| Model | Alias, category, upstream model mapping |
| Route | Target, provider kind, vLLM pool |
| Protocol | Ingress endpoint, adapter path, streaming |
| Outcome | Success, policy denial, rate limit, route failure, upstream failure |

## Measures

| Measure | Meaning |
| --- | --- |
| Request count | Accepted, denied, attempted, successful, failed |
| Token pressure | Prompt, completion, total, cached/reused where known |
| Latency | End-to-end, time to first token where known, attempt timing |
| Retry pressure | Extra attempts and replay cost |
| Fallback pressure | Frequency and cost of route fallback |
| Concurrency pressure | Active request or stream periods where captured |
| Context efficiency | Repeated long-context cost relative to cache/reuse evidence |

## Token Efficiency Posture

The initial product should focus on measurable inference and context efficiency:

- Prompt-heavy versus completion-heavy mix.
- Long-context repeats by project and model path.
- Cached or reused prompt evidence from upstreams that expose it.
- Retry and fallback token overhead.
- Token consumption per successful request or task-shaped project cohort when task labels exist.

The blueprint does not yet define a business-outcome efficiency score.

## Derived Indicators

| Indicator | Formula Direction |
| --- | --- |
| Prompt share | Prompt tokens divided by total tokens |
| Retry overhead | Attempt tokens beyond first successful logical path |
| Fallback share | Requests or tokens that used fallback |
| Successful token efficiency | Successful requests relative to token pressure |
| Context reuse indicator | Cached/reused prompt evidence relative to prompt pressure where source supports it |
| Pressure concentration | Top users/projects/models/pools share within a window |

Derived indicators must display missing-source caveats when upstreams do not expose a required fact.

## Reporting Windows

- Near-real-time operator view for recent pressure.
- Hourly and daily rollups for trend inspection.
- Comparable windows for buying or reallocating capacity.
- Retention tiers that keep aggregates longer than high-grain request facts when appropriate.

Exact retention values remain an operations decision.

## Dashboard Surfaces

| View | Must Answer |
| --- | --- |
| Pressure overview | What changed in the selected window? |
| Project view | Which project drives tokens, requests, retries, and context cost? |
| Actor view | Which users or services create pressure? |
| Model and pool view | Which model/pool is saturated or inefficient? |
| Protocol and adapter view | Which path introduces degradation, retry, or fallback cost? |
| Capacity report | Is the evidence strong enough to buy or shift servers? |

## Storage Posture

- PostgreSQL is the starting durable fact and rollup store.
- Redis can hold online counters or short-lived coordination state.
- Rollups and retention should control write volume before a heavier analytical datastore is introduced.
- A later storage TDR defines escalation triggers if query or retention pressure exceeds the starting posture.

## Privacy And Redaction

- Analytics is metadata-first.
- Prompt and response bodies are not required for the first capacity questions.
- Any content sampling decision requires explicit retention and audit control.
- Hashing or classifying request shape should avoid rebuilding sensitive prompt storage by accident.

## Not Owned Here

- Live alert thresholds and on-call pages.
- Exact audit event authority.
- Billing invoices and payment settlement.
- Business-value outcome metrics without labeled outcome facts.

## Acceptance Checks

1. A capacity dashboard can attribute pressure by time, subject, project, model category, route, and pool.
2. Retry and fallback cost do not disappear inside one request total.
3. Context-efficiency claims show whether cache/reuse facts are authoritative, estimated, or absent.
4. The analytics fact model does not require prompt bodies to answer first-phase questions.
