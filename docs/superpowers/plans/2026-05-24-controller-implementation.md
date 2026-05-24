# Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the FastAPI controller for the enterprise LLM gateway MVP.

**Architecture:** The backend is a single FastAPI service with clear internal modules for settings, database models/session, auth/policy, Redis-backed limits, LiteLLM invocation, usage/audit persistence, admin CRUD, and proxy request routes. PostgreSQL and Redis connectivity are configured but endpoint-backed tests are deferred until the user provides real endpoints.

**Tech Stack:** FastAPI, SQLModel async, SQLAlchemy async, asyncpg, redis asyncio, LiteLLM, Pydantic Settings, uv.

---

### Task 1: Dependencies And Package Layout

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/llm_gateway/__init__.py`
- Create: `src/llm_gateway/main.py`

- [ ] Add FastAPI, SQLModel async, asyncpg, redis, LiteLLM, Pydantic Settings, and uvicorn dependencies with `uv add`.
- [ ] Create the importable `llm_gateway` package under `src/`.
- [ ] Replace the placeholder root `main.py` with an app launcher shim.

### Task 2: Settings, Database, And Models

**Files:**
- Create: `src/llm_gateway/core/config.py`
- Create: `src/llm_gateway/db/session.py`
- Create: `src/llm_gateway/db/models.py`

- [ ] Load `.env.local` and environment variables.
- [ ] Provide async SQLAlchemy engine/session factories.
- [ ] Define SQLModel tables for subjects, projects, keys, model aliases, entitlements, upstreams, router command configs, rate policies, audit events, and request facts.

### Task 3: Security And Policy Services

**Files:**
- Create: `src/llm_gateway/services/security.py`
- Create: `src/llm_gateway/services/policy.py`
- Create: `src/llm_gateway/services/rate_limit.py`

- [ ] Hash and verify gateway keys with stdlib HMAC/SHA-256.
- [ ] Resolve bearer keys to active subjects/projects.
- [ ] Enforce model entitlement and per-model IP allowlists.
- [ ] Add Redis-backed request window and active concurrency helpers.

### Task 4: LiteLLM, Facts, And Router Commands

**Files:**
- Create: `src/llm_gateway/services/litellm_client.py`
- Create: `src/llm_gateway/services/facts.py`
- Create: `src/llm_gateway/services/router_command.py`

- [ ] Wrap LiteLLM async completion and streaming calls.
- [ ] Normalize usage from LiteLLM response/chunks without estimating missing usage.
- [ ] Persist request facts and audit events.
- [ ] Generate deterministic vLLM Router commands from stored config.

### Task 5: API Routes

**Files:**
- Create: `src/llm_gateway/api/deps.py`
- Create: `src/llm_gateway/api/proxy.py`
- Create: `src/llm_gateway/api/admin.py`
- Create: `src/llm_gateway/api/health.py`

- [ ] Implement shared request/session dependencies.
- [ ] Implement `/v1/chat/completions` and `/v1/messages` proxy controllers.
- [ ] Implement admin CRUD for subjects, projects, keys, model aliases, upstreams, router commands, and usage summaries.
- [ ] Implement health and diagnostics endpoints.

### Task 6: Application Assembly

**Files:**
- Modify: `src/llm_gateway/main.py`
- Modify: `main.py`

- [ ] Wire routers into the FastAPI app.
- [ ] Add startup diagnostics for LiteLLM version.
- [ ] Keep the root launcher compatible with `uv run python main.py`.

### Verification Handoff

After controller implementation is complete, ask the user for PostgreSQL and Redis endpoints before running endpoint-backed tests. Use `uv run ...` commands for all verification.
