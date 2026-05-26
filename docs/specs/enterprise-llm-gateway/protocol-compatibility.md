# Protocol Compatibility

## Purpose

This contract prevents "OpenAI-compatible" from becoming an untested promise. Every adapter path needs a request matrix, response matrix, streaming behavior, usage behavior, and unsupported-feature behavior.

## Contract Metadata

| Field | Value |
| --- | --- |
| Scope | Protocol adapter subsets and normalization behavior |
| Owner | Protocol compatibility |
| Consumers | Routing, adapters, testing, operator validation, analytics |
| Evidence | Request/response matrices, streaming rules, usage and error normalization |
| Open decisions | Concrete Anthropic and Codex first-slice field subsets need request-corpus evidence |

## Protocol Posture

| Path | Product Need | Contract Posture |
| --- | --- | --- |
| Gateway legacy OpenAI chat-facing shape to Anthropic | Required | Explicit subset mapped to native Anthropic behavior |
| Codex legacy-facing path to OpenAI Responses | Required for Codex route family | Explicit bridge, not universal shim |
| Gateway legacy OpenAI `v1/completions` | Removed from scope | Historical requirement; no MVP support planned |
| Arbitrary provider compatibility | Future | Requires its own matrix |

The gateway should prefer explicit adapters over relying on upstream silent field ignoring.

## Matrix Vocabulary

| Status | Meaning |
| --- | --- |
| Preserve | Semantics intentionally preserved |
| Normalize | Semantics represented in gateway canonical shape and mapped |
| Degrade | Feature works with a documented loss or narrower behavior |
| Reject | Gateway returns explicit unsupported behavior |
| Defer | Not in the current adapter contract |

## Canonical Envelope

Each adapter receives a canonical envelope after identity and policy:

- Gateway request ID.
- Downstream protocol family and endpoint path.
- Model alias and resolved model category.
- Request body plus selected supported extras.
- Streaming mode, timeout, and cancellation context.
- Caller/project attribution for evidence, not for prompt mutation.

Adapters may map protocol fields. They may not alter caller policy, route eligibility, or audit semantics.

## vLLM Legacy Completion Matrix

### Request Matrix

| Legacy Completion Field | vLLM Router Pool Contract | Status |
| --- | --- | --- |
| `model` | Resolve alias before upstream call | Normalize |
| `prompt` text or list | Forward only supported completion shape | Preserve |
| `max_tokens` | Forward when accepted by pool | Preserve |
| `temperature`, `top_p` | Forward when accepted by pool | Preserve |
| `stream` | Preserve with gateway stream lifecycle | Preserve |
| `stop` | Forward within route limits | Preserve |
| `n` | Support only if pool and response normalization handle it | Defer |
| `logprobs` or token detail | Route capability gate required | Defer |
| `suffix` | Do not pretend it works on vLLM completion path | Reject |
| vLLM-only extras | Allowlist per pool; never universal pass-through | Normalize |

### Response Matrix

| Concern | Contract |
| --- | --- |
| Choices | Preserve only the accepted completion subset |
| Usage | Normalize prompt, completion, total, and cache facts when available |
| Finish reason | Preserve when route supplies it; otherwise use explicit normalized mapping |
| Streaming | Preserve event ordering expected by supported clients or fail the route |
| Errors | Redact upstream detail and keep an adapter failure class |

## OpenAI Chat-Facing To Anthropic Matrix

### Request Matrix

| Chat-Facing Feature | Anthropic Adapter Contract | Status |
| --- | --- | --- |
| Model alias | Resolve to Anthropic target model mapping | Normalize |
| System/developer messages | Define one ordered Anthropic system mapping rule | Normalize |
| User and assistant text messages | Map supported text blocks | Preserve |
| Tool definitions | Support only chosen tool subset | Defer |
| Tool result messages | Support only with matching call mapping | Defer |
| JSON or strict structured-output promises | Do not promise schema guarantees without native capability proof | Reject |
| Audio input | No silent stripping | Reject |
| Unsupported OpenAI-only fields | Return matrix-defined reject or degrade result | Reject |

### Behavior Notes

- System/developer message mapping must be deterministic and reviewable.
- Prompt-caching assumptions are adapter-specific. A gateway conversion layer must not imply that Anthropic and vLLM cache semantics match.
- Native Anthropic features such as citations, PDF processing, extended thinking, or provider-specific caching require separate route capability rows.

### Response Matrix

| Chat-Facing Response Feature | Contract |
| --- | --- |
| Text content | Normalize back to the supported downstream shape |
| Tool calls | Only emit when request matrix enabled the tool subset |
| Usage | Map provider usage fields into normalized usage facts and raw provider detail reference if retained |
| Stop reason | Normalize with provider-specific reason detail available to operators |
| Streaming | Map stream events only for the subset that preserves client expectations |

## Codex Bridge To Responses Matrix

### Scope

This path exists for Codex-facing workflows that need OpenAI Responses upstream while keeping the downstream gateway surface controlled. It is not a claim that every legacy OpenAI field can be converted to Responses.

### Request Matrix

| Legacy-Facing Feature | Responses Adapter Contract | Status |
| --- | --- | --- |
| Model alias | Resolve to Responses model mapping | Normalize |
| System instruction | Map to explicit instructions/input posture | Normalize |
| User/assistant text history | Map into approved Responses item sequence | Normalize |
| Tool definitions and calls | Map only the Codex-approved tool subset | Normalize |
| Structured output shape | Translate only when route matrix proves equivalent shape | Defer |
| Storage/state option | Must follow explicit retention configuration | Normalize |
| Unsupported legacy params | Fail explicitly or record approved degradation | Reject |

### Response Matrix

| Responses Feature | Downstream Contract |
| --- | --- |
| Output text | Emit supported legacy-facing text shape |
| Tool call items | Emit only approved legacy-facing tool shape |
| Reasoning or provider-specific items | Do not leak undocumented upstream item types |
| Usage | Normalize usage and keep upstream detail class when needed |
| Response ID and state | Expose only if the downstream route contract permits it |

## Common Streaming Contract

1. Streaming route support is capability-gated.
2. Client disconnect cancels the upstream attempt when the upstream supports cancellation.
3. Mid-stream adapter failure produces explicit telemetry and usage outcome markers.
4. The gateway does not buffer a long stream only to fabricate a different protocol transcript.
5. Timeout and retry behavior must distinguish before-first-byte from mid-stream failure.

## Usage Normalization

The normalized usage fact model should represent:

- Prompt/input tokens.
- Completion/output tokens.
- Total tokens.
- Cached or reused prompt token facts when an upstream exposes them.
- Estimated versus authoritative marker.
- Upstream provider detail reference when exact mapping is unavailable.

Never invent token counts to fill a dashboard gap. Missing or estimated facts must remain visible.

## Error Normalization

| Error Family | Example |
| --- | --- |
| Client request | Invalid body, unsupported matrix field |
| Policy | Unauthorized, forbidden, IP denied, rate limited |
| Route | No eligible compatible healthy target |
| Upstream | Timeout, overload, upstream status, stream reset |
| Adapter | Mapping failure, malformed upstream response |

Downstream error bodies should be stable enough for clients. Operator evidence should retain richer redacted diagnostics.

## Adapter Review Checklist

- Matrix rows exist for request, response, streaming, usage, and errors.
- Rejected fields are rejected before upstream call when possible.
- Degraded behavior is user-visible or operator-visible by contract.
- Provider-specific capability flags are not copied to every route.
- Tests cover unsupported fields that upstreams might otherwise ignore.

## Acceptance Checks

1. The vLLM completion path explicitly rejects unsupported suffix behavior.
2. The Anthropic adapter never silently strips unsupported audio input.
3. The Codex Responses bridge identifies its approved subset and state/retention posture.
4. Streaming, usage, and error normalization are specified separately from happy-path field mapping.
