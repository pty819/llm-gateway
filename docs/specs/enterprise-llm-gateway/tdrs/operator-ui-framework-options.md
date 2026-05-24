# TDR: Operator UI Framework Options

## Status

Open for review. Current bias: choose the lighter framework the team can ship and maintain confidently.

## Decision

Choose SolidJS or Svelte for the operator console.

## Context

The UI is an operational tool with tables, filters, revisions, diffs, dashboards, forms, and audit drilldowns. It should remain light without becoming a bespoke frontend experiment.

## Decision Drivers

1. Team fluency and maintainability.
2. Data-heavy operator workflow ergonomics.
3. Build tooling and component ecosystem.
4. Type safety and form/state patterns.
5. Performance under dense tables and live-ish pressure views.
6. Ability to keep UI and admin API boundaries clear.

## Candidate Matrix

| Criterion | SolidJS | Svelte |
| --- | --- | --- |
| Reactive performance | Strong | Strong |
| Mental model | Fine-grained reactive | Compiler-driven component model |
| Ecosystem familiarity | Smaller than dominant React world | Broad and approachable |
| Operational UI suitability | Strong with chosen table/form libraries | Strong with chosen table/form libraries |
| Hiring risk | Moderate | Low to moderate |
| Bundle-light posture | Strong | Strong |

## Option Notes

### SolidJS

- Attractive for reactive, compact operator interfaces.
- Good when the team likes explicit fine-grained reactive primitives.
- Needs a deliberate component and data-fetching stack choice.

### Svelte

- Attractive for approachable components and light UI development.
- Good default when broad maintainability and onboarding matter.
- Still needs a disciplined data-grid, form, and API-client posture.

## Provisional Recommendation

Prefer Svelte if no team preference exists because the console is a maintainability-heavy operator surface. Prefer SolidJS when the implementation team has stronger Solid fluency and has a clear component stack for dense admin workflows.

## Invalidation Triggers

- Chosen framework lacks the table, form, visualization, or accessibility path required by the console.
- Team fluency changes the delivery risk materially.
- Admin API/data-fetching architecture reveals a stronger framework fit.

## Evidence Needed Before Closure

- One representative screen spike: route inventory plus policy edit validation.
- One representative analytics screen spike: pressure table with filters and chart.
- Component-library and data-grid shortlist.
