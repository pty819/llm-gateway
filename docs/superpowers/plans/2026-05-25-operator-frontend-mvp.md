# Operator Frontend MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a SvelteKit operator console over the current FastAPI gateway backend.

**Architecture:** The frontend is an operational console with route-level data loading, a small typed API client, compact resource tables, modal/drawer forms, and explicit secret redaction. It consumes only backend capabilities that exist now and keeps richer analytics or process supervision out of scope.

**Tech Stack:** SvelteKit, TypeScript, Vite, Playwright, Vitest, CSS variables or a lightweight component layer, Lucide icons.

---

## File Structure

- Create: `frontend/package.json` for scripts and dependencies.
- Create: `frontend/src/lib/api/client.ts` for admin client and error normalization.
- Create: `frontend/src/lib/api/types.ts` for backend resource types and enums.
- Create: `frontend/src/lib/state/admin-token.ts` for admin token state.
- Create: `frontend/src/lib/validators/*.ts` for CIDR, URL, port, rate policy, JSON object validation.
- Create: `frontend/src/lib/components/*` for app shell, tables, badges, dialogs, form controls, command block, JSON viewer.
- Create: `frontend/src/routes/*` for Overview, Models, Upstreams, Access, Projects, Policies, Router Commands, Usage, Audit, Diagnostics.
- Create: `frontend/tests/*` for unit and Playwright tests.

## Task 1: SvelteKit Skeleton

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/src/app.html`
- Create: `frontend/src/routes/+layout.svelte`
- Create: `frontend/src/routes/+page.svelte`

- [ ] Create the SvelteKit app under `frontend/`.
- [ ] Add scripts: `dev`, `build`, `test`, `test:e2e`, `lint`.
- [ ] Add a layout with left navigation and a top status bar.
- [ ] Add a placeholder Overview route that renders without backend data.
- [ ] Run `cd frontend && npm run build`.

## Task 2: Typed API Client

**Files:**
- Create: `frontend/src/lib/api/types.ts`
- Create: `frontend/src/lib/api/client.ts`
- Create: `frontend/src/lib/state/admin-token.ts`
- Test: `frontend/src/lib/api/client.test.ts`

- [ ] Define backend enum string unions exactly as documented in `api-client-and-state.md`.
- [ ] Implement `AdminClient.get/post/patch`.
- [ ] Inject `x-admin-token`.
- [ ] Normalize FastAPI and gateway error shapes into one `ApiError`.
- [ ] Unit test successful JSON, validation error, 401 error, and adapter error normalization.

## Task 3: Token Gate And Runtime Status

**Files:**
- Create: `frontend/src/lib/components/AdminTokenGate.svelte`
- Create: `frontend/src/lib/components/RuntimeStrip.svelte`
- Modify: `frontend/src/routes/+layout.svelte`
- Modify: `frontend/src/routes/+page.svelte`

- [ ] Token gate calls `GET /admin/diagnostics`.
- [ ] Invalid token shows the backend error.
- [ ] Runtime strip calls `GET /health/ready` and `GET /admin/diagnostics`.
- [ ] Overview shows readiness and LiteLLM version.
- [ ] E2E test valid and invalid token flows.

## Task 4: Shared Resource UI

**Files:**
- Create: `frontend/src/lib/components/ResourceTable.svelte`
- Create: `frontend/src/lib/components/StateBadge.svelte`
- Create: `frontend/src/lib/components/EntitySelect.svelte`
- Create: `frontend/src/lib/components/FormDrawer.svelte`
- Create: `frontend/src/lib/components/JsonViewer.svelte`
- Create: `frontend/src/lib/components/SecretOnceDialog.svelte`
- Test: component tests for badge, secret dialog, JSON redaction.

- [ ] Build compact table with loading/empty/error states.
- [ ] Build state/type badges for backend enums.
- [ ] Build entity select from loaded resources.
- [ ] Build drawer/dialog primitives with keyboard focus behavior.
- [ ] Build redacted JSON viewer and one-time key dialog.

## Task 5: Subjects, Projects, Memberships, Keys

**Files:**
- Create routes under `frontend/src/routes/access/subjects`, `frontend/src/routes/access/keys`, and `frontend/src/routes/projects`.
- Create forms under `frontend/src/lib/forms/access`.
- E2E tests under `frontend/tests/access.spec.ts`.

- [ ] Implement subject list/create/edit/state.
- [ ] Implement project list/create/edit.
- [ ] Implement membership create/list display.
- [ ] Implement gateway key issue/list/state.
- [ ] Ensure plaintext key appears only in `SecretOnceDialog`.
- [ ] E2E test onboarding subject -> project -> key.

## Task 6: Models, Entitlements, Upstreams

**Files:**
- Create: `frontend/src/routes/models/+page.svelte`
- Create: `frontend/src/routes/models/[id]/+page.svelte`
- Create: `frontend/src/routes/upstreams/+page.svelte`
- Create: `frontend/src/routes/policies/entitlements/+page.svelte`
- Create forms under `frontend/src/lib/forms/models`.
- E2E tests under `frontend/tests/model-access.spec.ts`.

- [ ] Implement model alias list/create/edit including IP allowlist.
- [ ] Implement CIDR editor and validation.
- [ ] Implement entitlement list/create/state.
- [ ] Implement upstream list/create/edit.
- [ ] Implement upstream health check button and result state.
- [ ] E2E test model alias -> upstream -> entitlement -> health check.

## Task 7: Rate Policies And Router Commands

**Files:**
- Create: `frontend/src/routes/policies/rate-limits/+page.svelte`
- Create: `frontend/src/routes/router-commands/+page.svelte`
- Create forms under `frontend/src/lib/forms/policies`.
- Create: `frontend/src/lib/components/CommandBlock.svelte`
- E2E tests under `frontend/tests/policy-router.spec.ts`.

- [ ] Implement rate policy list/create/edit/state.
- [ ] Scope selector offers only key, subject, project.
- [ ] Implement router command list/create/edit.
- [ ] Worker URL repeater validates URL shape.
- [ ] Render returned command in copyable command block.
- [ ] E2E test creating a rate policy and copying router command.

## Task 8: Usage, Audit, Diagnostics

**Files:**
- Create: `frontend/src/routes/usage/+page.svelte`
- Create: `frontend/src/routes/audit/+page.svelte`
- Create: `frontend/src/routes/diagnostics/+page.svelte`
- E2E tests under `frontend/tests/evidence.spec.ts`.

- [ ] Implement usage summary table with start/end controls and client-side filters.
- [ ] Implement audit table with redacted JSON detail drawer.
- [ ] Implement diagnostics page with readiness, LiteLLM version, and upstream health matrix.
- [ ] E2E test empty and populated usage/audit states.

## Task 9: Visual And Accessibility Verification

**Files:**
- Modify: frontend routes and components as needed.
- Add: `frontend/tests/visual.spec.ts`

- [ ] Run Playwright desktop screenshots at 1440x900.
- [ ] Run Playwright mobile screenshots at 390x844.
- [ ] Verify no overlapping text or broken controls.
- [ ] Verify keyboard navigation through token gate, navigation, forms, and dialogs.
- [ ] Fix defects before final build.

## Task 10: Final Verification

**Commands:**

```bash
uv run python scripts/init_db.py
uv run python scripts/seed_dev.py
cd frontend && npm run lint
cd frontend && npm run test
cd frontend && npm run build
cd frontend && npm run test:e2e
```

- [ ] Confirm all commands pass.
- [ ] Confirm no secrets are committed.
- [ ] Confirm frontend only claims capabilities supported by the backend.
