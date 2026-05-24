# Information Architecture

## App Shell

The frontend is a single operator console. It should not include a marketing landing page.

Primary shell regions:

- Left navigation: product areas.
- Top bar: environment, readiness indicator, admin-token status, refresh button.
- Main content: resource table or dashboard.
- Right drawer/dialog: create/edit/detail actions.

## Routes

| Route | Screen | Primary Backend Calls |
| --- | --- | --- |
| `/` | Overview | `GET /health/ready`, `GET /admin/diagnostics`, `GET /admin/usage/summary`, `GET /admin/audit-events` |
| `/models` | Model aliases | aliases, upstreams, entitlements, router configs |
| `/models/:id` | Model alias detail | same lists filtered client-side |
| `/upstreams` | Upstreams | `GET/POST/PATCH /admin/upstreams`, health check |
| `/access/subjects` | Subjects | subjects, memberships, keys |
| `/access/keys` | Gateway keys | keys, subjects, projects |
| `/projects` | Projects | projects, memberships, usage summary |
| `/policies/entitlements` | Entitlements | entitlements, aliases, subjects, projects, keys |
| `/policies/rate-limits` | Rate policies | rate policies, scope resource lists |
| `/router-commands` | vLLM Router command configs | router command configs, aliases |
| `/usage` | Usage summary | `GET /admin/usage/summary` |
| `/audit` | Audit events | `GET /admin/audit-events` |
| `/diagnostics` | Runtime diagnostics | readiness, diagnostics, optional upstream health matrix |

## Navigation Groups

| Group | Items |
| --- | --- |
| Operate | Overview, Diagnostics |
| Configure | Models, Upstreams, Router Commands |
| Access | Subjects, Keys, Projects |
| Policy | Entitlements, Rate Limits |
| Evidence | Usage, Audit |

## Cross-Linking

- Subject rows link to keys, memberships, entitlements, and usage filtered by subject ID.
- Project rows link to memberships, keys, entitlements, and usage filtered by project ID.
- Model alias rows link to upstreams, entitlements, router configs, and usage filtered by alias.
- Upstream rows link back to model alias detail.
- Audit rows link to resource IDs when the resource type is known by the frontend.

## Data Loading Strategy

MVP can load list endpoints per screen and apply client-side joins for display labels. This is acceptable for hundreds of internal resources. Avoid making backend assumptions that require thousands-row pagination until backend endpoints provide it.

## Empty States

| Screen | Empty State Action |
| --- | --- |
| Models | Create model alias |
| Upstreams | Create upstream for selected model alias |
| Subjects | Create user or service account |
| Keys | Issue gateway key |
| Projects | Create project |
| Entitlements | Grant model access |
| Rate limits | Create rate policy |
| Router commands | Create router command config |
| Usage | Prompt for a wider time range or send traffic |
| Audit | No privileged changes recorded yet |
