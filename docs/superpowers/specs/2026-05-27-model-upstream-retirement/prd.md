# PRD: Model Alias And Upstream Retirement

## Objective

Let administrators recover from bad or obsolete model endpoints after real usage
exists, without losing historical usage facts.

## Problem

Deleting a model alias can return `409 conflict` when the alias has upstreams that
have already been used. The backend refuses to delete used upstreams because
`request_facts.upstream_target_id` references them. The frontend also lacks a way
to edit an existing upstream Base URL, so operators cannot fix a broken endpoint
without deleting/recreating records.

## Decision

Historical usage facts are authoritative and must stay. Operational resources such
as upstream endpoints and model aliases may be retired. Before deleting an
upstream, detach historical facts by setting `request_facts.upstream_target_id` to
null. Keep `request_facts.model_alias` as the durable human-readable historical
alias string.

## Scope

### In Scope

- Allow deleting used upstreams by detaching historical facts.
- Allow deleting model aliases with cascade-upstreams even if upstreams have usage
  history.
- Keep the existing first-step conflict when deleting a model alias with upstreams
  but without `cascade_upstreams`; the UI uses this to ask for confirmation.
- Add frontend controls to edit existing upstream endpoint fields.
- Add regression tests for used upstream deletion and used model alias cascade.

### Out Of Scope

- Deleting historical request facts.
- Renaming historical model alias strings in request facts.
- A full soft-delete lifecycle for every resource.
- Bulk endpoint migration tooling.

## Acceptance Criteria

- Deleting a used upstream succeeds.
- After deleting a used upstream, matching `request_facts` rows still exist and
  their `upstream_target_id` is null.
- Deleting a used model alias with `cascade_upstreams=true` succeeds.
- Historical `request_facts.model_alias` remains readable after alias deletion.
- Deleting a model alias with upstream dependencies and no cascade still returns a
  structured `409` so the UI can confirm cascade.
- Admin UI exposes edit controls for existing upstream Base URL and related fields.
