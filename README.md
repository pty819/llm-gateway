# LLM Gateway

FastAPI + Svelte enterprise LLM gateway for internal model serving. It sits in front of OpenAI-compatible vLLM Router or direct vLLM endpoints, uses LiteLLM for protocol conversion, and owns identity, access control, request limits, usage facts, and operator workflows.

## What It Does

- Self-service user registration and login.
- Automatic gateway key issuance for registered users.
- Built-in `guest` and `admin` teams.
- Team-based model permissions: a user can use the union of models granted to all of their active teams.
- Admin account and admin console for users, teams, model grants, keys, upstreams, rate limits, router commands, usage, and audit.
- OpenAI-compatible `/v1/chat/completions` proxy.
- OpenAI-compatible `/v1/responses` proxy (for Codex and other Responses API clients).
- Anthropic-compatible `/v1/messages` proxy through LiteLLM.
- `/v1/models` returns only the models the caller can use.
- Model-level IP allowlists.
- Redis-backed RPM and concurrency limits.
- PostgreSQL-backed audit and token/request usage facts.
- Admin-only DuckDB PostgreSQL extension queries for manual heavy usage analytics.
- Redis-backed realtime runtime metrics for active upstream connections plus cached direct-vLLM and vLLM Router `/metrics` pressure.
- vLLM Router command generation for same-model endpoint pools.

## Stack

- Backend: FastAPI, async SQLAlchemy/SQLModel, PostgreSQL, Redis, LiteLLM.
- Frontend: SvelteKit.
- Package/runtime: `uv` for Python, npm for frontend.

## Local Configuration

Copy `.env.example` to `.env.local` and fill in real values:

```bash
cp .env.example .env.local
```

Important settings:

```bash
LLM_GATEWAY_DATABASE_URL=postgresql+asyncpg://...
LLM_GATEWAY_REDIS_URL=redis://...

LLM_GATEWAY_UPSTREAM_BASE_URL=https://api.example.com/v1
LLM_GATEWAY_UPSTREAM_MODEL=actual-upstream-model-name
LLM_GATEWAY_UPSTREAM_API_KEY=upstream-provider-key
LLM_GATEWAY_LITELLM_MODEL=openai/actual-upstream-model-name

LLM_GATEWAY_ADMIN_TOKEN=dev-admin-token
LLM_GATEWAY_BOOTSTRAP_ADMIN_USERNAME=admin
LLM_GATEWAY_BOOTSTRAP_ADMIN_PASSWORD=dev-admin-password

# Enabled by default so Vite/reverse-proxy calls preserve model IP allowlists.
LLM_GATEWAY_TRUST_PROXY_HEADERS=true
LLM_GATEWAY_TRUST_PROXY_CIDRS=127.0.0.0/8,::1/128
```

`.env.local` must stay untracked because it contains upstream credentials.

## Start

Initialize or migrate the database:

```bash
uv run python scripts/init_db.py
```

Run this command on every backend upgrade before starting the new server. The script is intentionally idempotent: it stamps a legacy schema that already has gateway tables but no Alembic version, then upgrades to the current migration head. This keeps PostgreSQL aligned with the backend without asking operators to hand-edit tables.

For local upgrades that should also sync Python and frontend dependencies:

```bash
uv run python scripts/upgrade_local.py
```

DuckDB is pinned to `1.5.3` because DuckDB extensions are version/platform-bound. The repository vendors the PostgreSQL scanner extension under `vendor/duckdb/extensions/v1.5.3/<platform>/`. Runtime loads the matching local artifact first:

- Linux x64 deployment: `linux_amd64`
- Apple Silicon development: `osx_arm64`

Refresh the bundled extension artifacts with:

```bash
uv run python scripts/fetch_duckdb_extensions.py
```

Useful flags:

```bash
uv run python scripts/upgrade_local.py --skip-frontend-install
uv run python scripts/upgrade_local.py --skip-python-sync
uv run python scripts/upgrade_local.py --skip-db
```

For deployments, make the startup order explicit:

```bash
uv run python scripts/init_db.py
uv run python main.py
```

For local development, one command can upgrade and start both backend and frontend:

```bash
uv run python scripts/start_local.py --host 127.0.0.1
```

`start_local.py` automatically enables trusted proxy headers for the local Vite proxy, so model IP allowlists see the real browser client IP instead of Vite's backend connection address.

On a LAN server, expose both services on the machine IP:

```bash
uv run python scripts/start_local.py --host 10.21.48.65
```

This release adds analytics indexes through Alembic, so run the upgrade step before starting the new backend.
The admin heavy analytics panel uses DuckDB's PostgreSQL extension for longer time windows. Normal user self-usage stays PostgreSQL-backed so it remains fresh and subject-scoped.
The usage page also opens an authenticated SSE stream over `fetch` to display realtime upstream load. Direct vLLM endpoints expose engine metrics such as token/s, running/waiting requests, KV cache usage, and prefix-cache signal. vLLM Router exposes a different `vllm_router_*` metrics family, so the page shows router workers, running requests, worker load, cache hit ratio, request count, and error count separately instead of treating router metrics as worker engine metrics. The gateway auto-detects the metric family from the Prometheus response; you do not need to manually label an upstream as vLLM or Router.

For direct vLLM, leaving the upstream Metrics URL empty is usually enough because the gateway derives `<base-url-without-/v1>/metrics`. For vLLM Router, set the upstream Metrics URL to the Router Prometheus endpoint, for example `http://router-host:29000/metrics` or the address configured with `--prometheus-host/--prometheus-port`; Router metrics are commonly not served from the OpenAI API port. Each upstream metrics response is cached for 3 seconds in Redis. If an upstream has no metrics endpoint, returns 404/timeout, or exposes unrelated Prometheus metrics only, the realtime metrics scrape is ignored instead of adding a failed row to the dashboard.

Optionally seed a development upstream/model:

```bash
uv run python scripts/seed_dev.py
```

Start the backend:

```bash
uv run python main.py
```

Backend default:

```text
http://127.0.0.1:18080
```

Start the frontend:

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

If you start backend and frontend separately and clients call `/v1` through the Vite URL, start the backend with trusted proxy headers enabled:

```bash
LLM_GATEWAY_TRUST_PROXY_HEADERS=true \
LLM_GATEWAY_TRUST_PROXY_CIDRS=127.0.0.0/8,::1/128 \
uv run python main.py
```

For a LAN reverse proxy or a Vite process that reaches the backend through the server's LAN address, add that proxy IP as a `/32`, for example `127.0.0.0/8,::1/128,10.21.48.65/32`.

Frontend default:

```text
http://127.0.0.1:5173
```

## Login And Registration

Open the frontend and sign in with the bootstrap admin account:

```text
username: admin
password: dev-admin-password
```

Override those defaults with:

```bash
LLM_GATEWAY_BOOTSTRAP_ADMIN_USERNAME=...
LLM_GATEWAY_BOOTSTRAP_ADMIN_PASSWORD=...
```

The older local operator token is still supported as a fallback:

```text
x-admin-token: dev-admin-token
```

New users can self-register. Registration creates:

- one `Subject`
- one personal `Project`
- one gateway key, shown once
- one active membership in `guest`
- one login session

Users can later log in and issue more personal gateway keys from the user dashboard.

## Team Permission Model

Teams control model access.

Effective model access is:

```text
legacy direct entitlement OR active team membership with active model-team grant
```

The normal team flow:

1. Admin creates teams such as `team1`, `team2`, `team3`.
2. Admin adds users to one or more teams.
3. Admin grants models to one or more teams.
4. A user's available models are the union of all active grants from all active teams they belong to.

Example:

```text
team1 -> models a, b, c
team2 -> models b, c, d
team3 -> model e
user -> team1 + team3
usable models -> a, b, c, e
```

## Admin Team Management

All team operations are on the **Teams** page in the admin console.

### Create A Team

Fill in **Name** and optional **Notes**, click **Create team**.

Built-in teams (`guest`, `admin`) are created automatically and marked with a **builtin** badge. `guest` is the default team every self-registered user joins. `admin` always has access to every model.

### Add A User To A Team

Under **Add user to team**, select the team and the subject, set a role (defaults to `member`), click **Add membership**.

The user immediately gains access to all models granted to that team. No restart required.

### Remove A User From A Team

In the **Memberships** table, click **Disable** on the membership row. The user loses access to that team's models on the next request. Click **Activate** to restore.

### Grant A Model To A Team

Under **Grant model to team**, select the model alias and the team, click **Grant model**.

Every active member of that team can now call this model. Revoking works the same way: disable the grant row.

### Typical Onboarding Workflow

1. User self-registers on the frontend. They join `guest` automatically and receive a gateway key.
2. Admin creates a team (e.g. `research`) on the Teams page.
3. Admin adds the user to `research`.
4. Admin grants models (e.g. `qwen3`, `deepseek`) to `research`.
5. User logs in, sees the new models on their dashboard, and can call them immediately.

Built-in behavior:

- self-registered users join `guest`
- bootstrap admin joins `admin`
- every model alias is automatically granted to `admin`

## Entitlements (Direct Model Access)

Entitlements are an alternative to team-based grants for giving a specific entity access to a model. They are useful when you need per-user, per-key, or per-project control that does not fit the team model.

Manage entitlements on the **Entitlements** page in the admin console.

### Create An Entitlement

1. Select a **Model** (model alias).
2. Select a **Scope**: `project`, `subject`, or `key`.
3. Select the **Target** (the specific project, user, or key).
4. Click **Grant access**.

### Scope Semantics

| Scope | Effect |
|-------|--------|
| `project` | All keys owned by this project can use the model. |
| `subject` | All keys owned by this user can use the model. |
| `key` | Only this specific gateway key can use the model. |

### Enable / Disable

Each entitlement row has an Enable/Disable toggle. Disabling immediately revokes access without deleting the record. Re-enable at any time.

### Entitlements vs Team Grants

- **Team grants** are the recommended default — add a user to a team and grant models to the team.
- **Entitlements** are for one-off or exception cases — e.g. giving a visiting collaborator access to a single model without creating a whole team.

Both paths are OR'd together: a user can use a model if they have a direct entitlement OR a team grant.

## Rate Limits

Rate limits control how many requests per minute (RPM) and how many concurrent requests a caller can make. They are enforced via Redis counters.

Manage rate policies on the **Rate Limits** page in the admin console.

### Default Values

When no custom rate policy exists, the environment defaults apply:

| Setting | Default | Environment Variable |
|---------|---------|----------------------|
| RPM | 120 | `LLM_GATEWAY_DEFAULT_RPM` |
| Concurrency | 8 | `LLM_GATEWAY_DEFAULT_CONCURRENCY` |

Override in `.env.local`:

```bash
LLM_GATEWAY_DEFAULT_RPM=200
LLM_GATEWAY_DEFAULT_CONCURRENCY=16
```

### Create A Rate Policy

On the **Rate Limits** page:

1. Select **Scope**: `key`, `subject`, or `project`.
2. Select the **Target** (the specific key, user, or project).
3. Enter **RPM** (requests per minute) — leave empty to inherit default.
4. Enter **Concurrency** (max simultaneous requests) — leave empty to inherit default.
5. Click **Create policy**.

### Scope Semantics

| Scope | Effect |
|-------|--------|
| `key` | Limit applies to a single gateway key. |
| `subject` | Limit applies to all keys owned by this user. |
| `project` | Limit applies to all keys in this project. |

### Resolution Rules

When a request arrives, the gateway resolves the effective limit by combining all matching active policies:

1. Start with the environment defaults.
2. Look up active policies matching the request's key, subject, and project.
3. Take the **minimum** across all matching policies and defaults.

This means the most restrictive policy always wins. Example:

```text
default RPM:           120
key-level RPM:         60
subject-level RPM:     100
effective RPM:         min(120, 60, 100) = 60
```

### Enable / Disable

Each rate policy row has an Enable/Disable toggle. Disabling makes it inactive (the default or other policies take over) without deleting the record.

### Typical Rate Limit Scenarios

| Scenario | How |
|----------|-----|
| All users get default limits | Do nothing — defaults apply automatically. |
| Heavy user needs more headroom | Create a `subject` policy with higher RPM and concurrency. |
| Shared key needs throttling | Create a `key` policy with lower RPM and concurrency. |
| Cap usage for a project | Create a `project` policy to limit total project throughput. |
| Temporary burst access | Create a policy, then disable it when no longer needed. |

## Add A vLLM Router Aggregated Model

Suppose you have 3 vLLM endpoints all serving `qwen3` with API key `qwne4`:

```text
http://gpu-a:8000
http://gpu-b:8000
http://gpu-c:8000
```

### Step 1 — Create A Model Alias (Models page)

This is the name clients will use in their requests.

```text
Alias:              qwen3
Upstream model:     qwen3
LiteLLM model:      openai/qwen3
```

### Step 2 — Generate A Router Command (Router Commands page)

```text
Model:   qwen3
Name:    qwen3-pool
Policy:  consistent_hash
Port:    18001

Worker URLs:
http://gpu-a:8000
http://gpu-b:8000
http://gpu-c:8000
```

The UI generates a command like:

```bash
vllm-router --worker-urls http://gpu-a:8000 http://gpu-b:8000 http://gpu-c:8000 --policy consistent_hash --host 0.0.0.0 --port 18001
```

Run this command on a machine that can reach all 3 endpoints. The router listens on `:18001` and load-balances across the pool. The gateway does not start or supervise this process for you in the MVP.

### Step 3 — Create An Upstream (Upstreams page)

Point it at the **router**, not individual endpoints.

```text
Model:       qwen3
Name:        qwen3-router
Base URL:    http://router-host:18001/v1
API Key:     qwne4
Health path: /models
```

Use the **Check** button to verify the router is reachable.

### Step 4 — Grant Access (Teams or Entitlements page)

Team-based model access (recommended):

1. Create teams such as `research`, `infra`.
2. Add users to teams.
3. Grant `qwen3` to the teams that need it.

Or use Entitlements to grant access to a specific subject, project, or individual gateway key.

A user's available models are the union of all active grants from all their active teams plus any direct entitlements.

## Client Usage

OpenAI Chat Completions:

```bash
curl http://gateway-host:18080/v1/chat/completions \
  -H "Authorization: Bearer <gateway-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3",
    "messages": [{"role": "user", "content": "hello"}],
    "stream": true
  }'
```

OpenAI Responses API (for Codex and similar clients):

```bash
curl http://gateway-host:18080/v1/responses \
  -H "Authorization: Bearer <gateway-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3",
    "input": "hello",
    "stream": true
  }'
```

Anthropic Messages:

```bash
curl http://gateway-host:18080/v1/messages \
  -H "x-api-key: <gateway-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3",
    "max_tokens": 128,
    "messages": [{"role": "user", "content": "hello"}]
  }'
```

List available models:

```bash
curl http://gateway-host:18080/v1/models \
  -H "Authorization: Bearer <gateway-key>"
```

## Verification

Backend:

```bash
uv run python scripts/init_db.py
uv run pytest -q
```

Frontend:

```bash
cd frontend
npm run check
npm run test
npm run build
npm run test:e2e
```

## Current MVP Boundaries

- vLLM Router process management is not automatic yet; the UI generates commands only.
- SSO is intentionally out of scope; registration, login, self-service password change, and admin password reset are handled by the gateway.
- Gateway keys are shown once when issued; existing keys are listed only by prefix.
- ClickHouse or other heavyweight analytics stores are intentionally not used.
