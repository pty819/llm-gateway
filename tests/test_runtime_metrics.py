from __future__ import annotations

import time
from typing import Any, cast

import pytest

from llm_gateway.services.runtime_metrics import (
    ACTIVE_KEY,
    RuntimeRouteInfo,
    VLLM_METRICS_CACHE_PREFIX,
    VLLM_METRICS_LOCK_PREFIX,
    VLLMMetricsTarget,
    mark_connection_closed,
    mark_connection_open,
    metrics_url_from_base_url,
    parse_vllm_prometheus_metrics,
    runtime_snapshot,
)


pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeRedis:
    def __init__(self) -> None:
        self.zsets: dict[str, dict[str, float]] = {}
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.values: dict[str, str] = {}
        self.sequence = 0

    async def get(self, name: str) -> str | None:
        return self.values.get(name)

    async def setex(self, name: str, seconds: int, value: str) -> None:
        del seconds
        self.values[name] = value

    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        del ex
        if nx and name in self.values:
            return False
        self.values[name] = value
        return True

    async def zadd(self, name: str, mapping: dict[str, float]) -> None:
        self.zsets.setdefault(name, {}).update(mapping)

    async def zrem(self, name: str, member: str) -> None:
        self.zsets.setdefault(name, {}).pop(member, None)

    async def zremrangebyscore(self, name: str, minimum: Any, maximum: Any) -> None:
        zset = self.zsets.setdefault(name, {})
        max_score = float(maximum)
        for member, score in list(zset.items()):
            if score <= max_score:
                zset.pop(member, None)

    async def zrange(self, name: str, start: int, end: int) -> list[str]:
        items = sorted(
            self.zsets.setdefault(name, {}).items(), key=lambda item: item[1]
        )
        if end == -1:
            return [member for member, _ in items[start:]]
        return [member for member, _ in items[start : end + 1]]

    async def xadd(
        self,
        name: str,
        fields: dict[str, str],
        *,
        id: str = "*",
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> None:
        del approximate
        if id == "*":
            stream_id = f"{int(time.time() * 1000)}-{self.sequence}"
        else:
            stream_id = id.replace("*", str(self.sequence))
        self.sequence += 1
        stream = self.streams.setdefault(name, [])
        stream.append((stream_id, fields))
        if maxlen and len(stream) > maxlen:
            del stream[: len(stream) - maxlen]

    async def xrange(
        self, name: str, *, min: str = "-", max: str = "+"
    ) -> list[tuple[str, dict[str, str]]]:
        del max
        start_ms = 0 if min == "-" else int(min.split("-", 1)[0])
        return [
            (stream_id, fields)
            for stream_id, fields in self.streams.setdefault(name, [])
            if int(stream_id.split("-", 1)[0]) >= start_ms
        ]


async def test_runtime_snapshot_groups_active_connections():
    redis = cast(Any, FakeRedis())
    info_a = RuntimeRouteInfo(
        upstream_id="up-a", upstream_name="上游 A", model_alias="model-a"
    )
    info_b = RuntimeRouteInfo(
        upstream_id="up-b", upstream_name="上游 B", model_alias="model-b"
    )

    member_a = await mark_connection_open(
        redis, request_id="req-a", info=info_a, now=100.0
    )
    await mark_connection_open(redis, request_id="req-b", info=info_b, now=100.0)

    snapshot = await runtime_snapshot(redis, window_seconds=10, now=100.0)

    assert snapshot["active_connections"] == 2
    assert snapshot["total_recent_tokens"] is None
    assert snapshot["total_tokens_per_second"] is None
    by_upstream = {row["upstream_id"]: row for row in snapshot["upstreams"]}
    assert by_upstream["up-a"]["active_connections"] == 1
    assert by_upstream["up-a"]["tokens_per_second"] is None
    assert by_upstream["up-b"]["recent_tokens"] is None

    await mark_connection_closed(redis, member_a)
    snapshot = await runtime_snapshot(redis, window_seconds=10, now=100.0)
    by_upstream = {row["upstream_id"]: row for row in snapshot["upstreams"]}
    assert "up-a" not in by_upstream
    assert by_upstream["up-b"]["active_connections"] == 1


async def test_runtime_snapshot_prunes_stale_active_connections():
    redis = cast(Any, FakeRedis())
    info = RuntimeRouteInfo(
        upstream_id="up-stale", upstream_name="Stale", model_alias="model"
    )
    await mark_connection_open(redis, request_id="stale", info=info, now=1.0)

    snapshot = await runtime_snapshot(redis, window_seconds=10, now=3_700.0)

    assert snapshot["active_connections"] == 0
    assert redis.zsets[ACTIVE_KEY] == {}


async def test_metrics_url_from_base_url_strips_openai_v1_path():
    assert (
        metrics_url_from_base_url("http://gpu-a:8000/v1") == "http://gpu-a:8000/metrics"
    )
    assert (
        metrics_url_from_base_url("http://gpu-a:8000/custom/v1")
        == "http://gpu-a:8000/custom/metrics"
    )
    assert metrics_url_from_base_url("http://gpu-a:8000") == "http://gpu-a:8000/metrics"


async def test_parse_vllm_prometheus_metrics_aggregates_selected_samples():
    metrics = parse_vllm_prometheus_metrics(
        """
# HELP vllm:num_requests_running Number of requests.
vllm:num_requests_running{model_name="a"} 2
vllm:num_requests_running{model_name="b"} 3
vllm:num_requests_waiting 4
vllm:kv_cache_usage_perc 0.75
vllm:prompt_tokens_total 100
vllm:generation_tokens_total 50
other_metric 999
"""
    )

    assert metrics["vllm:num_requests_running"] == 5
    assert metrics["vllm:num_requests_waiting"] == 4
    assert metrics["vllm:kv_cache_usage_perc"] == 0.75
    assert metrics["vllm:prompt_tokens_total"] == 100
    assert "other_metric" not in metrics


async def test_parse_vllm_router_prometheus_metrics_keeps_router_samples():
    metrics = parse_vllm_prometheus_metrics(
        """
vllm_router_active_workers 2
vllm_router_worker_load{worker="a"} 3
vllm_router_worker_load{worker="b"} 4
vllm_router_running_requests{worker="a"} 5
vllm_router_cache_hits_total 9
vllm_router_cache_misses_total 1
process_cpu_seconds_total 99
"""
    )

    assert metrics["vllm_router_active_workers"] == 2
    assert metrics["vllm_router_worker_load"] == 7
    assert metrics["vllm_router_running_requests"] == 5
    assert "process_cpu_seconds_total" not in metrics


async def test_runtime_snapshot_uses_cached_vllm_metrics_and_counter_delta():
    redis = cast(Any, FakeRedis())
    target = VLLMMetricsTarget(
        upstream_id="up-vllm",
        upstream_name="vLLM",
        model_alias="model",
        base_url="http://gpu-a:8000/v1",
        extra_headers={},
    )
    calls = 0

    async def fetcher(_target):
        nonlocal calls
        calls += 1
        total = 100 if calls == 1 else 160
        return f"""
vllm:num_requests_running 2
vllm:num_requests_waiting 1
vllm:kv_cache_usage_perc 0.5
vllm:prefix_cache_queries 10
vllm:prefix_cache_hits 7
vllm:prompt_tokens {total}
vllm:generation_tokens 0
"""

    first = await runtime_snapshot(
        redis, vllm_targets=[target], metrics_fetcher=fetcher, now=100.0
    )
    second = await runtime_snapshot(
        redis, vllm_targets=[target], metrics_fetcher=fetcher, now=101.0
    )
    for key in list(redis.values):
        if key.startswith(VLLM_METRICS_CACHE_PREFIX) or key.startswith(
            VLLM_METRICS_LOCK_PREFIX
        ):
            redis.values.pop(key)
    third = await runtime_snapshot(
        redis, vllm_targets=[target], metrics_fetcher=fetcher, now=110.0
    )

    assert calls == 2
    assert third["upstreams"][0]["vllm"]["kind"] == "vllm"
    assert first["upstreams"][0]["vllm"]["tokens_per_second"] is None
    assert second["upstreams"][0]["vllm"]["tokens_per_second"] is None
    assert third["upstreams"][0]["vllm"]["tokens_per_second"] == 6
    assert third["upstreams"][0]["vllm"]["prefix_cache_hit_ratio"] == 0.7


async def test_runtime_snapshot_summarizes_vllm_router_metrics_without_engine_pressure():
    redis = cast(Any, FakeRedis())
    target = VLLMMetricsTarget(
        upstream_id="router-a",
        upstream_name="vLLM Router",
        model_alias="model",
        base_url="http://router-a:9000/v1",
        extra_headers={},
    )

    async def fetcher(_target):
        return """
vllm_router_active_workers 2
vllm_router_worker_health{worker="a"} 1
vllm_router_worker_health{worker="b"} 1
vllm_router_worker_load{worker="a"} 3
vllm_router_worker_load{worker="b"} 4
vllm_router_running_requests{worker="a"} 5
vllm_router_cache_hits_total 9
vllm_router_cache_misses_total 1
vllm_router_requests_total{route="/v1/chat/completions"} 12
"""

    snapshot = await runtime_snapshot(
        redis, vllm_targets=[target], metrics_fetcher=fetcher, now=100.0
    )
    row = snapshot["upstreams"][0]

    assert row["vllm"]["kind"] == "vllm_router"
    assert row["vllm"]["running"] is None
    assert row["vllm"]["tokens_per_second"] is None
    assert row["vllm"]["router"]["active_workers"] == 2
    assert row["vllm"]["router"]["healthy_workers"] == 2
    assert row["vllm"]["router"]["worker_load"] == 7
    assert row["vllm"]["router"]["running_requests"] == 5
    assert row["vllm"]["router"]["cache_hit_ratio"] == 0.9
    assert snapshot["vllm"]["router"]["observed_upstreams"] == 1
    assert snapshot["vllm"]["router"]["active_workers"] == 2
    assert snapshot["vllm"]["configured_upstreams"] == 1
    assert snapshot["vllm"]["ignored_upstreams"] == 0


async def test_runtime_snapshot_uses_explicit_metrics_url():
    redis = cast(Any, FakeRedis())
    target = VLLMMetricsTarget(
        upstream_id="router-metrics",
        upstream_name="Router Metrics",
        model_alias="model",
        base_url="http://router-a:18001/v1",
        metrics_url="http://router-a:29000/metrics",
        extra_headers={},
    )

    async def fetcher(seen_target):
        assert seen_target.metrics_url == "http://router-a:29000/metrics"
        return """
vllm_router_active_workers 1
vllm_router_running_requests{worker="a"} 2
"""

    snapshot = await runtime_snapshot(
        redis, vllm_targets=[target], metrics_fetcher=fetcher, now=100.0
    )

    assert snapshot["upstreams"][0]["vllm"]["metrics_url"] == (
        "http://router-a:29000/metrics"
    )
    assert snapshot["upstreams"][0]["vllm"]["kind"] == "vllm_router"


async def test_runtime_snapshot_ignores_unsupported_metrics_targets():
    redis = cast(Any, FakeRedis())
    target = VLLMMetricsTarget(
        upstream_id="unsupported",
        upstream_name="Unsupported",
        model_alias="model",
        base_url="http://unsupported:8000/v1",
        extra_headers={},
    )

    async def fetcher(_target):
        return """
process_cpu_seconds_total 10
python_info 1
"""

    snapshot = await runtime_snapshot(
        redis, vllm_targets=[target], metrics_fetcher=fetcher, now=100.0
    )

    assert snapshot["upstreams"] == []
    assert snapshot["vllm"]["configured_upstreams"] == 1
    assert snapshot["vllm"]["observed_upstreams"] == 0
    assert snapshot["vllm"]["ignored_upstreams"] == 1
