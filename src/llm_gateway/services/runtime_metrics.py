from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx2 as httpx
from redis.asyncio import Redis

from llm_gateway.db.models import ModelAlias, UpstreamTarget


ACTIVE_KEY = "llm_gateway:runtime:active_connections"
DEFAULT_WINDOW_SECONDS = 10
ACTIVE_STALE_SECONDS = 60 * 60
VLLM_METRICS_CACHE_PREFIX = "llm_gateway:runtime:vllm_metrics"
VLLM_METRICS_LOCK_PREFIX = "llm_gateway:runtime:vllm_metrics_lock"
VLLM_METRICS_COUNTER_PREFIX = "llm_gateway:runtime:vllm_counter"
VLLM_METRICS_CACHE_SECONDS = 3
VLLM_METRICS_LOCK_SECONDS = 3
VLLM_METRICS_TIMEOUT_SECONDS = 1.5


@dataclass(frozen=True)
class RuntimeRouteInfo:
    upstream_id: str
    upstream_name: str
    model_alias: str


@dataclass(frozen=True)
class VLLMMetricsTarget:
    upstream_id: str
    upstream_name: str
    model_alias: str
    base_url: str
    extra_headers: dict[str, str]
    api_key: str | None = None
    metrics_url: str | None = None


def route_info(model_alias: ModelAlias, upstream: UpstreamTarget) -> RuntimeRouteInfo:
    return RuntimeRouteInfo(
        upstream_id=str(upstream.id),
        upstream_name=upstream.name,
        model_alias=model_alias.alias,
    )


def active_member(request_id: str, info: RuntimeRouteInfo) -> str:
    return json.dumps(
        {
            "request_id": request_id,
            "upstream_id": info.upstream_id,
            "upstream_name": info.upstream_name,
            "model_alias": info.model_alias,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


async def mark_connection_open(
    redis: Redis,
    *,
    request_id: str,
    info: RuntimeRouteInfo,
    now: float | None = None,
) -> str:
    member = active_member(request_id, info)
    await redis.zadd(ACTIVE_KEY, {member: now if now is not None else time.time()})
    return member


async def mark_connection_closed(redis: Redis, member: str) -> None:
    await redis.zrem(ACTIVE_KEY, member)


@asynccontextmanager
async def tracked_runtime_connection(redis: Redis, *, request_id: str, route):
    member = None
    with suppress(Exception):
        member = await mark_connection_open(
            redis,
            request_id=request_id,
            info=route_info(route.model_alias, route.upstream),
        )
    try:
        yield
    finally:
        if member:
            with suppress(Exception):
                await mark_connection_closed(redis, member)


async def runtime_snapshot(
    redis: Redis,
    *,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    vllm_targets: list[VLLMMetricsTarget] | None = None,
    metrics_fetcher=None,
    now: float | None = None,
) -> dict[str, Any]:
    now = now if now is not None else time.time()
    del window_seconds
    await redis.zremrangebyscore(ACTIVE_KEY, "-inf", now - ACTIVE_STALE_SECONDS)

    upstreams: dict[str, dict[str, Any]] = {}
    active_members = await redis.zrange(ACTIVE_KEY, 0, -1)
    for item in active_members:
        parsed = _decode_active_member(item)
        if not parsed:
            continue
        row = _upstream_row(upstreams, parsed)
        row["active_connections"] += 1

    targets = vllm_targets or []
    vllm_rows = await _collect_vllm_metrics(
        redis,
        targets=targets,
        now=now,
        fetcher=metrics_fetcher,
    )
    ignored_metrics = 0
    for item in vllm_rows:
        if _should_ignore_metrics(item["vllm"]):
            ignored_metrics += 1
            continue
        row = _upstream_row(upstreams, item)
        row["vllm"] = item["vllm"]

    rows = sorted(
        upstreams.values(),
        key=lambda row: (
            _sort_number(row.get("vllm", {}).get("tokens_per_second")),
            _sort_number(row.get("vllm", {}).get("running")),
            row["active_connections"],
        ),
        reverse=True,
    )
    vllm_summary = _global_vllm_summary(
        rows,
        configured_upstreams=len(targets),
        ignored_upstreams=ignored_metrics,
    )
    return {
        "generated_at": _iso_from_epoch(now),
        "window_seconds": VLLM_METRICS_CACHE_SECONDS,
        "metrics_cache_seconds": VLLM_METRICS_CACHE_SECONDS,
        "total_tokens_per_second": vllm_summary["tokens_per_second"],
        "total_recent_tokens": None,
        "active_connections": len(active_members),
        "vllm": vllm_summary,
        "upstreams": rows,
    }


def metrics_url_from_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[: -len("/v1")]
    if not path:
        path = ""
    return urlunparse(
        parsed._replace(path=f"{path}/metrics", params="", query="", fragment="")
    )


def metrics_url_for_target(target: VLLMMetricsTarget) -> str:
    if target.metrics_url:
        return target.metrics_url
    return metrics_url_from_base_url(target.base_url)


def parse_vllm_prometheus_metrics(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, value = _parse_prometheus_sample(line)
        if name is None or value is None:
            continue
        if not (name.startswith("vllm:") or name.startswith("vllm_router_")):
            continue
        values[name] = values.get(name, 0.0) + value
    return values


def summarize_vllm_metrics(
    metrics: dict[str, float],
    *,
    tokens_per_second: float | None = None,
) -> dict[str, Any]:
    prefix_queries = metrics.get("vllm:prefix_cache_queries")
    prefix_hits = metrics.get("vllm:prefix_cache_hits")
    prefix_hit_ratio = None
    if prefix_queries and prefix_queries > 0 and prefix_hits is not None:
        prefix_hit_ratio = prefix_hits / prefix_queries
    else:
        prefix_hit_ratio = _first_metric(
            metrics,
            "vllm:gpu_prefix_cache_hit_rate",
            "vllm:cpu_prefix_cache_hit_rate",
        )

    kind = _metrics_kind(metrics)
    prompt_total = _metric_sum(
        metrics, "vllm:prompt_tokens_total", "vllm:prompt_tokens"
    )
    generation_total = _metric_sum(
        metrics, "vllm:generation_tokens_total", "vllm:generation_tokens"
    )
    return {
        "ok": True,
        "kind": kind,
        "metrics_url": "",
        "scraped_at": "",
        "running": _first_metric(metrics, "vllm:num_requests_running"),
        "waiting": _first_metric(metrics, "vllm:num_requests_waiting"),
        "swapped": _first_metric(metrics, "vllm:num_requests_swapped"),
        "kv_cache_usage": _first_metric(
            metrics, "vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc"
        ),
        "cpu_cache_usage": _first_metric(metrics, "vllm:cpu_cache_usage_perc"),
        "prefix_cache_hit_ratio": prefix_hit_ratio,
        "prompt_tokens_total": prompt_total,
        "generation_tokens_total": generation_total,
        "tokens_total": prompt_total + generation_total,
        "tokens_per_second": tokens_per_second,
        "router": summarize_router_metrics(metrics) if kind == "vllm_router" else None,
    }


def summarize_router_metrics(metrics: dict[str, float]) -> dict[str, Any]:
    cache_hits = metrics.get("vllm_router_cache_hits_total")
    cache_misses = metrics.get("vllm_router_cache_misses_total")
    cache_hits_value = cache_hits or 0.0
    cache_misses_value = cache_misses or 0.0
    cache_queries = cache_hits_value + cache_misses_value
    return {
        "requests_total": metrics.get("vllm_router_requests_total"),
        "request_errors_total": metrics.get("vllm_router_request_errors_total"),
        "processed_requests_total": metrics.get("vllm_router_processed_requests_total"),
        "active_workers": metrics.get("vllm_router_active_workers"),
        "healthy_workers": metrics.get("vllm_router_worker_health"),
        "worker_load": metrics.get("vllm_router_worker_load"),
        "running_requests": metrics.get("vllm_router_running_requests"),
        "max_load": metrics.get("vllm_router_max_load"),
        "min_load": metrics.get("vllm_router_min_load"),
        "cache_hits_total": cache_hits,
        "cache_misses_total": cache_misses,
        "cache_hit_ratio": (
            cache_hits_value / cache_queries if cache_queries > 0 else None
        ),
    }


async def _collect_vllm_metrics(
    redis: Redis,
    *,
    targets: list[VLLMMetricsTarget],
    now: float,
    fetcher=None,
) -> list[dict[str, Any]]:
    return await asyncio.gather(
        *[
            _collect_vllm_target(redis, target=target, now=now, fetcher=fetcher)
            for target in targets
        ]
    )


async def _collect_vllm_target(
    redis: Redis,
    *,
    target: VLLMMetricsTarget,
    now: float,
    fetcher=None,
) -> dict[str, Any]:
    return {
        "upstream_id": target.upstream_id,
        "upstream_name": target.upstream_name,
        "model_alias": target.model_alias,
        "vllm": await _cached_vllm_metrics(
            redis, target=target, now=now, fetcher=fetcher
        ),
    }


async def _cached_vllm_metrics(
    redis: Redis,
    *,
    target: VLLMMetricsTarget,
    now: float,
    fetcher=None,
) -> dict[str, Any]:
    cache_key = f"{VLLM_METRICS_CACHE_PREFIX}:{target.upstream_id}"
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(_decode_value(cached))

    lock_key = f"{VLLM_METRICS_LOCK_PREFIX}:{target.upstream_id}"
    acquired = await redis.set(lock_key, "1", ex=VLLM_METRICS_LOCK_SECONDS, nx=True)
    if not acquired:
        return _ignored_metrics_snapshot(
            target,
            reason="refresh_in_progress",
            now=now,
        )

    try:
        metrics_text = (
            await fetcher(target)
            if fetcher is not None
            else await _fetch_vllm_metrics_text(target)
        )
        metrics = parse_vllm_prometheus_metrics(metrics_text)
        if metrics:
            tokens_per_second = await _vllm_token_rate(
                redis, target=target, metrics=metrics, now=now
            )
            snapshot = summarize_vllm_metrics(
                metrics, tokens_per_second=tokens_per_second
            )
            snapshot["metrics_url"] = metrics_url_for_target(target)
            snapshot["scraped_at"] = _iso_from_epoch(now)
        else:
            snapshot = _ignored_metrics_snapshot(
                target, reason="unsupported_metrics", now=now
            )
    except Exception as exc:
        snapshot = _ignored_metrics_snapshot(target, reason=str(exc), now=now)
    await redis.setex(
        cache_key,
        VLLM_METRICS_CACHE_SECONDS,
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
    )
    return snapshot


async def _fetch_vllm_metrics_text(target: VLLMMetricsTarget) -> str:
    headers = dict(target.extra_headers or {})
    if target.api_key:
        headers.setdefault("Authorization", f"Bearer {target.api_key}")
    async with httpx.AsyncClient(timeout=VLLM_METRICS_TIMEOUT_SECONDS) as client:
        response = await client.get(metrics_url_for_target(target), headers=headers)
    response.raise_for_status()
    return response.text


async def _vllm_token_rate(
    redis: Redis,
    *,
    target: VLLMMetricsTarget,
    metrics: dict[str, float],
    now: float,
) -> float | None:
    tokens_total = _metric_sum(
        metrics, "vllm:prompt_tokens_total", "vllm:prompt_tokens"
    ) + _metric_sum(metrics, "vllm:generation_tokens_total", "vllm:generation_tokens")
    if tokens_total <= 0:
        return None

    key = f"{VLLM_METRICS_COUNTER_PREFIX}:{target.upstream_id}"
    previous_raw = await redis.get(key)
    await redis.setex(
        key,
        60,
        json.dumps({"sampled_at": now, "tokens_total": tokens_total}),
    )
    if not previous_raw:
        return None

    try:
        previous = json.loads(_decode_value(previous_raw))
        previous_tokens = float(previous["tokens_total"])
        previous_at = float(previous["sampled_at"])
    except (KeyError, TypeError, ValueError):
        return None
    elapsed = now - previous_at
    if elapsed <= 0:
        return None
    delta = tokens_total - previous_tokens
    if delta < 0:
        return None
    return delta / elapsed


def _ignored_metrics_snapshot(
    target: VLLMMetricsTarget, *, reason: str, now: float
) -> dict[str, Any]:
    return {
        "ok": False,
        "ignore": True,
        "kind": "unknown",
        "metrics_url": metrics_url_for_target(target),
        "scraped_at": _iso_from_epoch(now),
        "error": reason[:500],
        "running": None,
        "waiting": None,
        "swapped": None,
        "kv_cache_usage": None,
        "cpu_cache_usage": None,
        "prefix_cache_hit_ratio": None,
        "prompt_tokens_total": None,
        "generation_tokens_total": None,
        "tokens_total": None,
        "tokens_per_second": None,
        "router": None,
    }


def _parse_prometheus_sample(line: str) -> tuple[str | None, float | None]:
    head, _, raw_value = line.rpartition(" ")
    if not head or not raw_value:
        return None, None
    name = head.split("{", 1)[0]
    try:
        value = float(raw_value)
    except ValueError:
        return None, None
    return name, value


def _first_metric(metrics: dict[str, float], *names: str) -> float | None:
    for name in names:
        if name in metrics:
            return metrics[name]
    return None


def _metric_sum(metrics: dict[str, float], *names: str) -> float:
    return sum(metrics.get(name, 0.0) for name in names)


def _metrics_kind(metrics: dict[str, float]) -> str:
    if any(name.startswith("vllm:") for name in metrics):
        return "vllm"
    if any(name.startswith("vllm_router_") for name in metrics):
        return "vllm_router"
    return "unknown"


def _global_vllm_summary(
    rows: list[dict[str, Any]],
    *,
    configured_upstreams: int,
    ignored_upstreams: int,
) -> dict[str, Any]:
    vllm_rows: list[dict[str, Any]] = []
    for row in rows:
        vllm = row.get("vllm")
        if isinstance(vllm, dict):
            vllm_rows.append(vllm)
    ok_rows = [row for row in vllm_rows if row.get("ok")]
    return {
        "configured_upstreams": configured_upstreams,
        "observed_upstreams": len(vllm_rows),
        "ok_upstreams": len(ok_rows),
        "ignored_upstreams": ignored_upstreams,
        "running": _sum_optional(ok_rows, "running"),
        "waiting": _sum_optional(ok_rows, "waiting"),
        "swapped": _sum_optional(ok_rows, "swapped"),
        "tokens_per_second": _sum_optional(ok_rows, "tokens_per_second"),
        "max_kv_cache_usage": _max_optional(ok_rows, "kv_cache_usage"),
        "router": _global_router_summary(ok_rows),
    }


def _should_ignore_metrics(snapshot: dict[str, Any]) -> bool:
    if snapshot.get("ignore"):
        return True
    return snapshot.get("kind") == "unknown"


def _global_router_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    router_rows = [
        row["router"]
        for row in rows
        if isinstance(row.get("router"), dict) and row.get("kind") == "vllm_router"
    ]
    return {
        "observed_upstreams": len(router_rows),
        "active_workers": _sum_optional(router_rows, "active_workers"),
        "healthy_workers": _sum_optional(router_rows, "healthy_workers"),
        "worker_load": _sum_optional(router_rows, "worker_load"),
        "running_requests": _sum_optional(router_rows, "running_requests"),
        "max_load": _max_optional(router_rows, "max_load"),
        "requests_total": _sum_optional(router_rows, "requests_total"),
        "request_errors_total": _sum_optional(router_rows, "request_errors_total"),
    }


def _sum_optional(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row.get(key) for row in rows if isinstance(row.get(key), int | float)]
    return sum(values) if values else None


def _max_optional(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row.get(key) for row in rows if isinstance(row.get(key), int | float)]
    return max(values) if values else None


def _sort_number(value: Any) -> float:
    return float(value) if isinstance(value, int | float) else -1.0


def _decode_active_member(value: Any) -> dict[str, Any] | None:
    try:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or "upstream_id" not in parsed:
        return None
    return parsed


def _decode_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _upstream_row(
    rows: dict[str, dict[str, Any]], item: dict[str, Any]
) -> dict[str, Any]:
    upstream_id = str(item["upstream_id"])
    row = rows.setdefault(
        upstream_id,
        {
            "upstream_id": upstream_id,
            "upstream_name": str(item.get("upstream_name") or upstream_id),
            "model_alias": str(item.get("model_alias") or ""),
            "tokens_per_second": None,
            "recent_tokens": None,
            "active_connections": 0,
        },
    )
    if item.get("upstream_name"):
        row["upstream_name"] = str(item["upstream_name"])
    if item.get("model_alias"):
        row["model_alias"] = str(item["model_alias"])
    return row


def _iso_from_epoch(value: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(value, tz=UTC).isoformat()
