# TDR: Gateway Language Options

## Status

Open for review. Current bias: choose the least exotic option that can sustain streaming proxy work, policy checks, and operator velocity under measured load.

## Decision

Choose the main gateway data-plane language among Go, C#, and Rust.

## Context

The gateway should support roughly hundreds of concurrent human users plus service bursts, long-lived streaming responses, policy checks, protocol adapters, telemetry, and PostgreSQL/Redis integration. Python is not the preferred data-plane baseline for this project because performance headroom and runtime predictability matter to the user.

## Decision Drivers

1. Streaming HTTP proxy ergonomics and backpressure.
2. Predictable concurrency and memory behavior.
3. Adapter development speed for evolving LLM protocols.
4. Observability, PostgreSQL, Redis, and auth ecosystem.
5. Hiring familiarity and operational maintainability.
6. Benchmarkability under realistic long-context traffic.

## Candidate Matrix

| Criterion | Go | C# | Rust |
| --- | --- | --- | --- |
| Streaming HTTP ergonomics | Strong | Strong | Strong with more type and lifetime surface |
| Runtime familiarity in infra teams | Often strong | Strong in .NET organizations | More specialized |
| Throughput headroom | Strong | Strong | Very strong |
| Adapter iteration speed | Strong | Strong | Moderate unless team is fluent |
| Memory/control precision | Moderate | Moderate to strong | Strongest |
| Ecosystem for web, metrics, PG, Redis | Strong | Strong | Strong but more choice friction |
| Operational complexity risk | Low to moderate | Low to moderate | Moderate |

## Option Notes

### Go

- Good fit for lean network services and streaming proxy work.
- Usually a low-friction infra hiring and deployment choice.
- Requires discipline around typed protocol adapters and config validation so flexibility does not become loose JSON glue.

### C#

- Strong async server and tooling ecosystem.
- Attractive when the team already has .NET depth or wants shared enterprise integration patterns.
- Runtime footprint and deployment posture should be benchmarked in the target environment rather than assumed.

### Rust

- Strongest control and headroom story.
- Attractive if the gateway becomes a highly performance-sensitive core maintained by a Rust-fluent team.
- Higher development and review cost is a real product risk for rapidly changing adapter contracts.

## Provisional Recommendation

Use Go as the default candidate for implementation planning unless team expertise, platform standardization, or benchmark results favor C#. Pick Rust only with explicit evidence that its performance/control advantage outweighs slower adapter and hiring velocity.

## Invalidation Triggers

- Go benchmark misses streaming or tail-latency targets under realistic policy and fact-emission load.
- Existing organization standards make C# materially easier to operate and staff.
- The gateway requires low-level memory/control behavior that Rust uniquely simplifies after measurement.

## Evidence Needed Before Closure

- One benchmark harness shape for streaming completions and long-context traffic.
- Team skill and operations survey.
- Library shortlist for HTTP streaming, telemetry, PostgreSQL, Redis, auth, and schema validation.
- Service topology choice: one service or split data/control plane.
