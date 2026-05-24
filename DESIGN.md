# Design

## Source Of Truth
- Status: Draft
- Last refreshed: 2026-05-25
- Primary product surfaces: operator console for an internal LLM gateway
- Evidence reviewed: `docs/superpowers/specs/2026-05-24-enterprise-llm-gateway-mvp-design.md`, `docs/specs/enterprise-llm-gateway/operator-ui-and-admin-apis.md`, `src/llm_gateway/api/admin.py`, `src/llm_gateway/api/proxy.py`, `tests/test_backend_integration.py`

## Brand
- Personality: quiet, operational, precise, trustworthy.
- Trust signals: redacted secrets, explicit state badges, visible health checks, audit trail links, exact timestamps.
- Avoid: marketing hero sections, decorative dashboards, oversized cards, one-color visual themes, hidden destructive actions.

## Product Goals
- Goals: let operators configure model access, upstreams, rate policy, router commands, and usage review from one console.
- Non-goals: public product landing page, end-user chat interface, SSO, billing UI, vLLM process supervisor.
- Success signals: an operator can issue a key, entitle a project to a model, verify upstream health, copy a router command, and inspect usage without touching SQL.

## Personas And Jobs
- Primary personas: internal platform operator, infra engineer, project lead checking usage pressure.
- User jobs: configure gateway resources, diagnose access denial, inspect pressure by project/model/user, prepare vLLM Router command lines, review privileged changes.
- Key contexts of use: production-ish internal operations, incident review, capacity planning, onboarding a new project or user.

## Information Architecture
- Primary navigation: Overview, Models, Upstreams, Access, Projects, Rate Limits, Router Commands, Usage, Audit, Diagnostics.
- Core routes/screens: list/detail/edit views for each backend resource; usage and audit are query-first views.
- Content hierarchy: status and failures first, then config inventory, then historical evidence.

## Design Principles
- Dense beats decorative: tables, filters, side panels, and inline state are preferred over large cards.
- Evidence before action: actions that affect traffic should show current state, related resource IDs, and expected effect.
- Secrets stay invisible: upstream keys and gateway key hashes are never rendered; plaintext gateway keys are shown once after creation.
- One gateway mental model: model alias is the central object tying IP policy, upstream, entitlement, router config, and usage together.

## Visual Language
- Color: neutral base with restrained semantic accents for active, disabled, warning, failure, and success.
- Typography: compact system sans; numeric columns use tabular figures.
- Spacing/layout rhythm: 8px grid, compact rows, persistent page header, split list/detail where useful.
- Shape/radius/elevation: 6px or less for panels and table containers; avoid nested cards.
- Motion: minimal; use loading skeletons and focus transitions only.
- Imagery/iconography: Lucide-style icons for actions and navigation; no illustrative hero art.

## Components
- Existing components to reuse: none yet.
- New components: app shell, resource table, detail drawer, state badge, secret-once dialog, CIDR editor, key-value JSON editor, health check result, router command block, usage summary table, audit event table.
- Variants and states: loading, empty, failed, disabled, active, copied, validation error, saving.
- Token/component ownership: frontend owns visual tokens; backend enum values remain the source of resource states.

## Accessibility
- Target standard: WCAG 2.1 AA for operator workflows.
- Keyboard/focus behavior: all table row actions, dialogs, tabs, copy buttons, and forms are keyboard reachable.
- Contrast/readability: semantic colors must pass contrast without relying on hue alone.
- Screen-reader semantics: tables use headers, dialogs have names, errors are associated with fields.
- Reduced motion: no required animation for understanding state.

## Responsive Behavior
- Supported breakpoints/devices: desktop first; tablet usable; mobile read-only-ish but forms must not break.
- Layout adaptations: side navigation collapses; detail drawers become full-screen panels on narrow screens.
- Touch/hover differences: all hover-only actions must also be visible on focus or row selection.

## Interaction States
- Loading: preserve table structure and show skeleton rows or progress text.
- Empty: explain which resource is absent and expose the primary create action.
- Error: show backend detail text when safe; keep retry action near the failed request.
- Success: show compact toast and update table row in place.
- Disabled: state badge plus disabled primary traffic actions.
- Offline/slow network: request-level pending state and retry; do not duplicate submissions.

## Content Voice
- Tone: concise operator language.
- Terminology: use backend names exactly: subject, project, gateway key, model alias, entitlement, upstream, router command config, rate policy, request fact.
- Microcopy rules: explain consequence, not implementation trivia; never show upstream secret values.

## Implementation Constraints
- Framework/styling system: SvelteKit preferred; Solid is acceptable only if implementation switches explicitly.
- Design-token constraints: avoid a single-hue palette; keep operational status colors semantic.
- Performance constraints: tables should handle hundreds of rows with client-side filtering first; pagination can follow backend support later.
- Compatibility constraints: admin calls use `x-admin-token`; gateway proxy calls use bearer or `x-api-key`.
- Test/screenshot expectations: route smoke tests for every primary screen, form validation tests for CIDR/rate scopes, screenshot checks for desktop and mobile layout.

## Open Questions
- [ ] Should the operator console store the admin token in local storage, session storage, or only memory? Owner: product/security. Impact: operator convenience versus accidental token persistence.
- [ ] Should frontend implementation start in SvelteKit or Solid? Owner: engineering. Impact: routing and component conventions.
- [ ] Should usage charts wait for time-bucket backend endpoints? Owner: product/backend. Impact: MVP can ship summary tables now and charts later.
