# Operational Telemetry

## Purpose

Operational telemetry explains runtime behavior, health, and incidents. Capacity analytics may consume summaries, but telemetry remains optimized for diagnosis and alerting.

## Contract Metadata

| Field | Value |
| --- | --- |
| Scope | Runtime metrics, logs, traces, health, alerts, and vLLM telemetry handoff |
| Owner | Operational telemetry |
| Consumers | Deployment, operator UI, incident response, capacity summaries |
| Evidence | Signal families, metric classes, span shape, health and loss posture |
| Open decisions | Telemetry backend and tuned alert thresholds remain deployment decisions |

## Telemetry Goals

1. See whether the gateway data plane, adapter layer, storage dependencies, and upstream pools are healthy.
2. Correlate a downstream request with route attempt and upstream failure evidence.
3. Observe streaming behavior, latency slices, rate-limit pressure, and backpressure.
4. Preserve vLLM pool signals that help explain serving pressure without duplicating vLLM internals.

## Signals

| Signal | Scope |
| --- | --- |
| Metrics | Rates, counters, histograms, health gauges |
| Logs | Structured redacted event details |
| Traces | Request and dependency spans with correlation |
| Health | Readiness, liveness, dependency and pool checks |
| Alerts | Runtime threshold or anomaly escalation |

## Gateway Metrics

### Request Metrics

- Request count by endpoint family, outcome family, model category, route target class.
- Request duration and time to first byte or first token when available.
- Active requests and active streams.
- Rejected request count by policy family.
- Rate-limit and concurrency-limit actions.

### Route Metrics

- Route-selection count by target and policy family.
- Upstream attempt count, retry count, fallback count.
- Upstream timeout, cancellation, overload, adapter-error families.
- Stream completion versus partial failure.

### Dependency Metrics

- PostgreSQL latency, pool pressure, write failure class.
- Redis latency, counter failure class, saturation class.
- Secret resolution failure class.
- Control-plane snapshot publication and data-plane refresh health.

## vLLM Pool Handoff

The gateway should ingest or link pool-level vLLM and router evidence when available:

- Request running and queue pressure.
- Prompt and generation token counters.
- Prefix cache query/hit and KV cache usage signals when exposed.
- Latency histograms such as request latency, prefill time, decode time, and time to first token.
- Router health and selection posture.

These signals explain pool health. Gateway request attribution remains a gateway analytics responsibility.

## Logs

Structured logs should include:

- Gateway request ID and attempt ID.
- Subject/project identifiers only in approved redacted form.
- Endpoint family, adapter path, route target, outcome class.
- Error class and bounded diagnostic detail.
- Config revision for policy and route snapshot.

Logs must not include:

- Upstream or downstream secret values.
- Full prompt/response bodies by default.
- Unbounded upstream error payloads.
- Raw authorization headers.

## Tracing

### Span Shape

1. Ingress span.
2. Identity/policy span.
3. Adapter validation span.
4. Route selection span.
5. Upstream attempt span.
6. Evidence emission span when material.

### Correlation Rules

- Keep one gateway request ID through logs, traces, route facts, and audit references.
- Attach upstream request ID when a route safely returns one.
- Avoid high-cardinality raw prompt or user-entered labels in metric attributes.

## Health Contract

| Health Surface | Meaning |
| --- | --- |
| Liveness | Process can serve control loop |
| Readiness | Data plane has usable config and required dependencies |
| Dependency health | PostgreSQL, Redis, secret resolver, upstream route class |
| Route validation | Target compatibility and credential reference can be checked |
| Drain state | New traffic stops while existing streams complete or expire |

Health should not call a route "ready" if required secret references or policy snapshots are absent.

## Alert Families

- Gateway availability and saturation.
- Upstream pool unavailable or high failure class.
- Rate-limit or policy-denial spike that may indicate misuse.
- Evidence pipeline degradation beyond loss policy.
- Redis or PostgreSQL latency and failure.
- Secret resolution or rotation failure.

Alert thresholds are deployment decisions and should be tuned after measured load.

## Telemetry And Analytics Split

| Question | Primary Contract |
| --- | --- |
| Why did this stream fail now? | Telemetry |
| Which project drove the last week of token pressure? | Capacity analytics |
| Who changed the route policy? | Audit |
| Did a vLLM pool show cache and queue pressure? | Telemetry, summarized for analytics |

## Loss And Backpressure Posture

- Request correctness beats telemetry completeness.
- Audit loss policy is stricter than ordinary diagnostic logging.
- Analytics fact delivery should surface gaps rather than silently backfill invented facts.
- Telemetry sinks must not block streaming indefinitely.

## Acceptance Checks

1. A gateway request can be correlated across ingress, policy, adapter, route attempt, and evidence emission.
2. vLLM pool metrics are treated as pool health evidence, not as a replacement for gateway project attribution.
3. Metric labels avoid prompt content and raw unbounded client identifiers.
4. Streaming failure and cancellation are observable separately from successful completion.
