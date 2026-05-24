# Testing And Acceptance

## Verification Stack

Preferred frontend verification:

- Unit tests for API client, validators, formatters, and redaction.
- Component tests for forms and tables.
- Playwright route smoke tests against a running backend.
- Screenshot checks at desktop and mobile widths after major layout work.

## Required Test Fixtures

Use backend-owned fixture creation through admin APIs where possible:

1. Create subject.
2. Create project.
3. Issue key.
4. Create model alias.
5. Create upstream.
6. Create entitlement.
7. Create rate policy.
8. Create router command config.

Do not seed frontend tests by writing directly to PostgreSQL unless a test explicitly covers backend migration or seed behavior.

## Screen Acceptance Matrix

| Screen | Acceptance |
| --- | --- |
| Token gate | Valid token enters app; invalid token shows error and does not persist app state |
| Overview | Shows readiness, diagnostics, usage totals, recent audit events |
| Models | Can create alias, edit IP policy, show state/capability badges |
| Upstreams | Can create upstream, edit redacted secret fields, run health check |
| Subjects | Can create user/service, edit notes, disable/activate |
| Projects | Can create project, edit owner/notes, add membership |
| Keys | Can issue key, show plaintext once, copy it, disable/activate key |
| Entitlements | Can grant model access to one scope and disable/activate grant |
| Rate limits | Can create key/subject/project policy and edit limits/state |
| Router commands | Can create config and copy rendered command |
| Usage | Can query time window and show grouped token/request rows |
| Audit | Can view latest privileged events with redacted JSON detail |
| Diagnostics | Can show LiteLLM version and run selected upstream health checks |

## Negative Cases

- Missing admin token blocks admin calls.
- Invalid admin token shows 401 state.
- CIDR editor rejects invalid CIDR before submit.
- Rate policy rejects unsupported scope labels in the UI.
- Upstream secret value is not displayed after create or list.
- Closing the gateway-key plaintext dialog removes the visible key.
- Router command form rejects empty worker URL list.
- Usage empty result shows empty state, not a broken chart.

## Accessibility Acceptance

- Navigation works by keyboard.
- Dialogs trap focus and restore focus on close.
- Tables expose header associations.
- Error messages are connected to form fields.
- Color is not the only indicator of state.

## Responsive Acceptance

Desktop:

- Navigation, table, and detail drawer fit without overlap at 1440px width.

Tablet:

- Navigation can collapse.
- Drawers remain readable.

Mobile:

- Tables become horizontally scrollable or stacked summaries.
- Forms do not overflow; action buttons remain reachable.

## Backend-Compatibility Acceptance

- No frontend screen depends on backend pagination.
- No frontend screen depends on delete endpoints.
- No frontend screen claims router process supervision.
- No frontend screen claims effective-rate preview.
- No frontend screen claims time-bucketed analytics.

## Completion Gate

The frontend MVP is complete when:

1. All admin resources implemented by the backend can be created/listed/updated or state-mutated where backend supports it.
2. Operators can complete the onboarding flow: subject -> project -> key -> model alias -> upstream -> entitlement -> health check.
3. Operators can complete the serving-support flow: router command config -> copy command -> usage review -> audit review.
4. Automated tests cover the positive and negative cases above.
5. Playwright screenshots show no overlapping text or broken layout on desktop and mobile.
