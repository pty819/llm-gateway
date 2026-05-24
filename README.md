# LLM Gateway

FastAPI + Svelte enterprise LLM gateway for internal model serving. It sits in front of OpenAI-compatible vLLM Router or direct vLLM endpoints, uses LiteLLM for protocol conversion, and owns identity, access control, request limits, usage facts, and operator workflows.

## What It Does

- Self-service user registration and login.
- Automatic gateway key issuance for registered users.
- Built-in `guest` and `admin` teams.
- Team-based model permissions: a user can use the union of models granted to all of their active teams.
- Admin account and admin console for users, teams, model grants, keys, upstreams, rate limits, router commands, usage, and audit.
- OpenAI-compatible `/v1/chat/completions` proxy.
- Anthropic-compatible `/v1/messages` proxy through LiteLLM.
- `/v1/models` returns only the models the caller can use.
- Model-level IP allowlists.
- Redis-backed RPM and concurrency limits.
- PostgreSQL-backed audit and token/request usage facts.
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
```

`.env.local` must stay untracked because it contains upstream credentials.

## Start

Initialize or migrate the database:

```bash
uv run python scripts/init_db.py
```

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

OpenAI-compatible clients:

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

Anthropic-compatible clients:

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
- Password reset and SSO are intentionally out of scope.
- Gateway keys are shown once when issued; existing keys are listed only by prefix.
- ClickHouse or other heavyweight analytics stores are intentionally not used.
