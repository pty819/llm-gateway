# LLM Gateway

FastAPI-based enterprise LLM gateway MVP for internal production use.

The controller owns gateway keys, model entitlements, model-level IP allowlists,
Redis-backed request/concurrency limits, LiteLLM-backed OpenAI/Anthropic proxying,
usage facts, audit events, upstream health checks, and vLLM Router command config.

Local verification uses `.env.local` for PostgreSQL, Redis, and a real
OpenAI-compatible upstream. Keep `.env.local` untracked.
