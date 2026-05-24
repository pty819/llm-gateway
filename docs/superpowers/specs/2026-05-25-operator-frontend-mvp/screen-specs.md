# Screen Specs

## Admin Token Gate

Purpose: authenticate admin API calls with the current backend token model.

Requirements:

- Show one token input and a "Connect" button.
- On connect, call `GET /admin/diagnostics`.
- If successful, enter the app shell.
- If failed, show the backend error detail and keep the token editable.
- Provide a clear token action that removes token from memory/storage.

## Overview

Purpose: answer "is the gateway usable right now?"

Data:

- `GET /health/ready`
- `GET /admin/diagnostics`
- `GET /admin/usage/summary`
- `GET /admin/audit-events`

Content:

- Readiness strip: Postgres, Redis, overall ready.
- Runtime facts: app name, environment, LiteLLM version.
- Usage totals: requests, prompt tokens, completion tokens, total tokens, success count, failure count.
- Recent audit table: timestamp, action, resource type, outcome.

Actions:

- Refresh all overview data.
- Navigate to Usage or Audit detail.

## Models

Purpose: manage model aliases and policy-relevant model settings.

List columns:

- Alias
- Upstream model name
- LiteLLM model string
- State
- IP policy
- Capability badges: streaming, tools, reasoning
- Created/updated timestamps

Create/edit fields:

- `alias`
- `upstream_model_name`
- `litellm_model`
- `supports_streaming`
- `supports_tools`
- `supports_reasoning`
- `ip_policy_mode`
- `ip_allowlist_cidrs`
- `notes`
- `state` on edit

Detail panels:

- Upstreams for this alias.
- Entitlements for this alias.
- Router command configs for this alias.
- Usage rows for this alias.

Validation:

- Alias required.
- LiteLLM model required.
- CIDR list validates before submit when policy is `allowlist`.
- Warn if `allowlist` has no CIDRs.

## Upstreams

Purpose: configure the OpenAI-compatible upstream or vLLM Router target behind a model alias.

List columns:

- Name
- Model alias
- Base URL
- Health path
- State
- Has API key
- Updated timestamp

Create/edit fields:

- `model_alias_id`
- `name`
- `base_url`
- `api_key_ref`
- `api_key_value`
- `health_path`
- `extra_headers`
- `state` on edit

Actions:

- Run health check.
- Copy base URL.
- Replace secret value.

Security:

- Never show stored secret value.
- Show `has_api_key`.
- On edit, leave secret fields blank unless replacing.

## Subjects

Purpose: manage users and service accounts.

List columns:

- Name
- Type
- State
- Notes
- Created/updated timestamps

Actions:

- Create subject.
- Edit name/notes.
- Activate or disable.
- Navigate to related keys, projects, entitlements, and usage.

## Projects

Purpose: group usage and access around work/project ownership.

List columns:

- Name
- Owner subject
- State
- Notes
- Created/updated timestamps

Actions:

- Create project.
- Edit project fields.
- Add membership.
- Navigate to usage and entitlements.

Constraint:

- Do not show project disable action until backend supports project state mutation.

## Gateway Keys

Purpose: issue and revoke gateway-owned keys.

List columns:

- Key prefix
- Name
- Subject
- Project
- State
- Expires at
- Created/updated timestamps

Create fields:

- `subject_id`
- `project_id`
- `name`

One-time plaintext dialog:

- Show plaintext key only immediately after create response.
- Provide copy button.
- Warn that it cannot be retrieved later.

Actions:

- Disable key.
- Reactivate key.
- Create replacement key.

## Entitlements

Purpose: grant model access to a project, subject, or gateway key.

List columns:

- Model alias
- Scope type
- Scope label
- State
- Created/updated timestamps

Create fields:

- `model_alias_id`
- scope selector: project, subject, or key
- selected scope ID

Actions:

- Disable/activate entitlement.

Validation:

- Require exactly one scope in UI.

## Rate Limits

Purpose: configure DB-backed request and concurrency policies.

List columns:

- Scope
- Scope label
- Requests per minute
- Concurrency limit
- State
- Created/updated timestamps

Create/edit fields:

- `scope`: key, subject, project
- `scope_id`
- `requests_per_minute`
- `concurrency_limit`
- `state` on edit

Copy:

- Explain that effective limit is the minimum active policy across key, subject, project, and environment defaults.

## Router Commands

Purpose: generate vLLM Router command lines from stored config.

List columns:

- Name
- Model alias
- Policy
- Host
- Port
- Worker URL count
- Updated timestamp

Create/edit fields:

- `model_alias_id`
- `name`
- `worker_urls`
- `policy`
- `host`
- `port`
- `extra_args`

Rendered command:

- Monospace block.
- Copy button.
- Label: "Generated only; the gateway does not start this process in MVP."

## Usage

Purpose: inspect pressure by model, subject, and project.

Data:

- `GET /admin/usage/summary`

Controls:

- Start datetime.
- End datetime.
- Client-side filters for model alias, subject, project.

Columns:

- Model alias
- Subject
- Project
- Request count
- Prompt tokens
- Completion tokens
- Total tokens
- Success count
- Failure count

MVP constraint:

- No time-series chart unless it is clearly derived client-side from the current grouped response. Prefer summary table.

## Audit

Purpose: inspect privileged backend changes.

Data:

- `GET /admin/audit-events`

Columns:

- Created at
- Actor subject ID
- Action
- Resource type
- Resource ID
- Outcome
- Detail

Detail behavior:

- JSON detail drawer.
- Long values wrap.
- Secret-looking values are masked by frontend display logic even if backend accidentally returns them.

## Diagnostics

Purpose: verify runtime and upstream health.

Data:

- `GET /health/ready`
- `GET /admin/diagnostics`
- `GET /admin/upstreams`
- `GET /admin/upstreams/{id}/health` on demand

Content:

- Ready checks.
- LiteLLM version.
- Upstream health matrix with manual check buttons.
