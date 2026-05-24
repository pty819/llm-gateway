# TDR: Storage And Analytics Posture

## Status

Open for review. Current bias: PostgreSQL plus Redis first.

## Decision

Choose the starting storage posture for configuration, policy, audit, usage facts, online rate state, and capacity analytics.

## Context

The user explicitly prefers PostgreSQL extensions plus Redis and does not want a heavy analytical system added by default. The product still needs time-window pressure analysis by user, project, model, route, and pool.

## Decision Drivers

1. Durable policy, audit, and config integrity.
2. Low-latency rate and concurrency coordination.
3. Fact ingestion and rollup feasibility.
4. Operator query latency for pressure drilldowns.
5. Retention cost and migration complexity.
6. Team operational burden.

## Candidate Postures

| Posture | Upside | Cost |
| --- | --- | --- |
| PostgreSQL plus Redis | Lean, durable, familiar, enough for bounded facts and rollups | Needs discipline on rollups, partitions, and request-path writes |
| PostgreSQL plus Redis plus separate OLAP store | Scales analytical scans and retention | More infra, more pipelines, more failure modes |
| Telemetry backend as analytics authority | Reuses observability estate | Weak durable business attribution and audit fit |

## Starting Recommendation

Start with:

- PostgreSQL for configuration, policy, audit, usage facts, attempt facts, and rollups.
- Redis for online counters, concurrency/rate state, and short-lived snapshots.
- Time-bounded fact retention plus longer rollups.
- Explicit request-path decoupling for fact emission where correctness permits.

## What This Does Not Mean

- PostgreSQL must never be supplemented later.
- Redis may act as audit authority.
- Every raw telemetry event belongs in PostgreSQL.
- Prompt/response content must be retained for capacity analysis.

## Escalation Triggers

Escalate to a heavier analytics decision only when measured evidence shows one or more:

- Required retained fact volume makes PostgreSQL storage or maintenance unacceptable.
- Drilldown queries stay too slow after indexes, partitioning, and rollups.
- Analytics write/aggregation work endangers request-path performance.
- Required high-concurrency analytics use cases exceed the lean posture.

## Rejected For Now

| Alternative | Reason |
| --- | --- |
| Heavy OLAP store on day one | Adds operational weight before load evidence |
| Redis-only facts | Not durable enough for audit and capacity history |
| Metrics-only analytics | Loses project/person attribution contract |

## Evidence Needed Before Closure

- Expected fact volume and retention assumptions.
- Initial dashboard query shapes.
- Rate/concurrency algorithm requirements.
- PostgreSQL operational baseline and backup posture.
