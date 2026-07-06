"""Health-check sidecar process.

Runs the upstream health-check loop in a dedicated process, isolated from the
main gateway's asyncio event loop. The main process can be frozen by LiteLLM's
synchronous paths (tiktoken, JSON deserialization, blocking logging) for
seconds at a time; when that happens, every concurrent health probe's timeout
fires at once on loop recovery, producing a fleet-wide false-positive disable.
A separate process has a separate GIL, so a main-process freeze can never
poison the health checker.

Run with: ``python -m llm_gateway.health_sidecar``
"""
