# Backend Capability Map

## Admin Authentication

All `/admin/*` routes require:

```http
x-admin-token: <admin token>
```

The first frontend version should provide an admin-token entry screen. The token may be held in memory by default, with an explicit "remember on this device" option only if implemented deliberately.

## Public Gateway Routes

| Capability | Method/Path | Frontend Use |
| --- | --- | --- |
| OpenAI chat proxy | `POST /v1/chat/completions` | Not an operator screen in MVP; useful only for optional smoke panel |
| Anthropic messages proxy | `POST /v1/messages` | Not an operator screen in MVP |
| List entitled models | `GET /v1/models` | Optional key diagnostics: test a gateway key sees expected models |

## Health And Diagnostics

| Capability | Method/Path | Response Shape | Frontend Use |
| --- | --- | --- | --- |
| Live check | `GET /health/live` | `{ ok: true }` | Basic service reachability |
| Ready check | `GET /health/ready` | `{ ok, checks: { postgres, redis } }` | Overview status strip |
| Diagnostics | `GET /admin/diagnostics` | `{ app_name, environment, litellm_version }` | Overview and footer build/runtime facts |

## Subject Management

| Capability | Method/Path | Notes |
| --- | --- | --- |
| Create subject | `POST /admin/subjects` | `name`, `type: user|service`, optional `notes` |
| List subjects | `GET /admin/subjects` | Ordered newest first |
| Update subject | `PATCH /admin/subjects/{subject_id}` | `name`, `notes` |
| Set subject state | `PATCH /admin/subjects/{subject_id}/state` | `state: active|disabled` |

Frontend requirements:

- Show type and state badges.
- Disable subjects instead of deleting.
- Use subject IDs in project owner, membership, entitlement, and key creation forms.

## Project Management

| Capability | Method/Path | Notes |
| --- | --- | --- |
| Create project | `POST /admin/projects` | `name`, optional `owner_subject_id`, optional `notes` |
| List projects | `GET /admin/projects` | Ordered newest first |
| Update project | `PATCH /admin/projects/{project_id}` | `name`, `owner_subject_id`, `notes` |
| Create membership | `POST /admin/project-memberships` | `project_id`, `subject_id`, `role` |
| List memberships | `GET /admin/project-memberships` | No delete/update endpoint yet |

Frontend requirements:

- Do not expose project disable UI until backend supports it.
- Membership management is append-only in MVP.

## Gateway Key Management

| Capability | Method/Path | Notes |
| --- | --- | --- |
| Issue key | `POST /admin/gateway-keys` | Returns redacted key metadata plus `plaintext_key` once |
| List keys | `GET /admin/gateway-keys` | `key_hash` is redacted to null |
| Set key state | `PATCH /admin/gateway-keys/{gateway_key_id}/state` | revoke/restore via `disabled|active` |

Frontend requirements:

- Plaintext key appears only in a one-time dialog after creation.
- Copy button is allowed in the one-time dialog.
- Key list shows prefix, owner subject, project, state, creation time.
- Rotation is modeled as issue new key plus disable old key.

## Model Alias And Entitlement Management

| Capability | Method/Path | Notes |
| --- | --- | --- |
| Create model alias | `POST /admin/model-aliases` | alias, upstream model name, LiteLLM model string, capability booleans, IP policy |
| List aliases | `GET /admin/model-aliases` | Ordered newest first |
| Update alias | `PATCH /admin/model-aliases/{model_alias_id}` | Supports state, capabilities, IP policy, notes |
| Create entitlement | `POST /admin/model-entitlements` | one of subject/project/key scope is required |
| List entitlements | `GET /admin/model-entitlements` | Ordered newest first |
| Set entitlement state | `PATCH /admin/model-entitlements/{entitlement_id}/state` | active/disabled |

Frontend requirements:

- Model alias detail should show IP policy, CIDRs, capabilities, upstreams, entitlements, router configs, and usage summary filtered client-side when possible.
- Entitlement form must enforce exactly one visible scope selection at a time for clarity, even though backend accepts any one-or-more scope.
- CIDR fields must validate client-side before submit.

## Upstream Management

| Capability | Method/Path | Notes |
| --- | --- | --- |
| Create upstream | `POST /admin/upstreams` | base URL, optional key ref/value, health path, headers |
| List upstreams | `GET /admin/upstreams` | `api_key_value` always redacted |
| Update upstream | `PATCH /admin/upstreams/{upstream_id}` | supports state and secret replacement |
| Health check | `GET /admin/upstreams/{upstream_id}/health` | returns redacted upstream plus status code/url |

Frontend requirements:

- Never render `api_key_value`.
- Show `has_api_key` boolean.
- Health check is a user-triggered action with loading/result state.
- Current backend expects one active upstream per model alias in practice; UI should warn if multiple active upstreams exist for one alias.

## Router Command Config Management

| Capability | Method/Path | Notes |
| --- | --- | --- |
| Create router config | `POST /admin/router-command-configs` | worker URLs, policy, host, port, extra args |
| List router configs | `GET /admin/router-command-configs` | returns config plus rendered command |
| Update router config | `PATCH /admin/router-command-configs/{config_id}` | returns updated rendered command |

Frontend requirements:

- Provide a worker URL repeater input.
- Policy options are `consistent_hash` and `cache_aware`.
- Render command in a copyable monospace block.
- Label clearly that this does not start or supervise vLLM Router.

## Rate Policy Management

| Capability | Method/Path | Notes |
| --- | --- | --- |
| Create policy | `POST /admin/rate-policies` | scope, scope_id, RPM, concurrency |
| List policies | `GET /admin/rate-policies` | Ordered newest first |
| Update policy | `PATCH /admin/rate-policies/{policy_id}` | limits and state |

Frontend requirements:

- Scope selector should only offer `key`, `subject`, and `project` because only those scopes are enforced.
- Limits may be blank to inherit default for that dimension.
- Effective policy is not returned by backend yet; UI should not claim it can preview final effective limits.

## Usage And Audit

| Capability | Method/Path | Notes |
| --- | --- | --- |
| Usage summary | `GET /admin/usage/summary?start=&end=` | grouped by model alias, subject ID, project ID |
| Audit events | `GET /admin/audit-events` | latest 200 events |

Frontend requirements:

- Usage MVP is a summary table with filters and totals, not rich time-series charts.
- Display zero token sums as aggregate zeros, but preserve missing usage semantics in row detail when exposed later.
- Audit detail JSON must be formatted and redacted by default.

## Current Backend Gaps That Frontend Must Respect

- No pagination or server-side filtering for most list endpoints.
- No delete endpoints.
- No project state mutation endpoint.
- No role-based admin authorization beyond static admin token.
- No dedicated charting library; rich pressure analytics now come from admin
  analytics APIs and are rendered as lightweight tables/bars.
- No vLLM Router process management.
- OpenAI `/v1/completions` is out of scope; Codex uses `/v1/responses`.
- No secret manager integration; upstream secret values are stored in the configured backend field and redacted on read.
