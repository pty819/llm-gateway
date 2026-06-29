# Upstream 健康巡检与自动禁用 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增加后台健康巡检循环，周期探测每个 ACTIVE upstream 的 `/models`，发现故障（5xx/网络错误/超时/非 404 的 4xx）立即自动禁用，复用现有 `state` 字段与过滤链路，管理员手动恢复。

**Architecture:** 新增 `services/health_checker.py` 作为后台 asyncio task，挂在 app lifespan 上（startup 起、shutdown 停），生命周期范式与现有 `facts_queue` 一致。巡检每 3 秒一轮，并发探测所有 ACTIVE upstream，故障者逐个开独立 DB session 写 DISABLED + 审计事件。路由层 `resolve_route_context` 已过滤 `state == ACTIVE`，无需改动。

**Tech Stack:** Python 3、FastAPI lifespan、httpx（探测）、SQLAlchemy async session、SQLModel、pytest（含 pytest-asyncio、monkeypatch）。

参考 spec：`docs/superpowers/specs/2026-06-29-upstream-health-check-auto-disable-design.md`

---

## File Structure

| 文件 | 责任 | 操作 |
|---|---|---|
| `src/llm_gateway/services/health_checker.py` | 健康判定纯函数 `classify_health`、巡检循环、`start()`/`stop()` 生命周期 | 新增 |
| `src/llm_gateway/core/config.py` | 三个巡检配置字段 | 修改 |
| `src/llm_gateway/main.py` | lifespan 挂载 `start()`/`stop()` | 修改 |
| `tests/test_health_checker.py` | `classify_health` 纯函数测试 + 巡检循环行为测试 | 新增 |

**不改动的文件（重要）：** `db/models.py`（复用现有 `state` 字段，无迁移）、`services/policy.py` 的 `resolve_route_context`（已过滤 ACTIVE）、`services/upstream_routing.py`、`api/realtime.py`、前端。

---

## Task 1: 配置字段

**Files:**
- Modify: `src/llm_gateway/core/config.py`（在 `session_ttl_hours` 字段后追加）

- [ ] **Step 1: 在 Settings 类里加三个字段**

在 `session_ttl_hours: int = Field(default=168, alias="LLM_GATEWAY_SESSION_TTL_HOURS")` 这一行之后插入：

```python
    # 后台健康巡检：周期探测每个 ACTIVE upstream 的 /models，故障自动禁用。
    # interval/timeout 默认 3s：发现延迟 ≤ 一个周期，探测本身有独立超时上限。
    # enabled 总开关供调试/排障时一键关闭。
    health_check_interval_seconds: float = Field(
        default=3.0, alias="LLM_GATEWAY_HEALTH_CHECK_INTERVAL_SECONDS"
    )
    health_check_timeout_seconds: float = Field(
        default=3.0, alias="LLM_GATEWAY_HEALTH_CHECK_TIMEOUT_SECONDS"
    )
    health_check_enabled: bool = Field(
        default=True, alias="LLM_GATEWAY_HEALTH_CHECK_ENABLED"
    )
```

- [ ] **Step 2: 验证配置可加载**

Run: `cd /Users/liyifan/llm_gateway && python -c "from llm_gateway.core.config import Settings; s=Settings(); print(s.health_check_interval_seconds, s.health_check_timeout_seconds, s.health_check_enabled)"`
Expected: `3.0 3.0 True`

- [ ] **Step 3: Commit**

```bash
git add src/llm_gateway/core/config.py
git commit -m "Add health check settings (interval/timeout/enabled)"
```

---

## Task 2: 健康判定纯函数（TDD）

**Files:**
- Create: `src/llm_gateway/services/health_checker.py`
- Test: `tests/test_health_checker.py`

- [ ] **Step 1: 写失败测试 `tests/test_health_checker.py`**

```python
from __future__ import annotations

import httpx
import pytest

from llm_gateway.services.health_checker import HealthVerdict, classify_health


@pytest.mark.parametrize(
    ("status_code", "exc", "expected_healthy", "expected_reason"),
    [
        (200, None, True, "ok"),
        (404, None, True, "ok"),
        (500, None, False, "http_5xx"),
        (502, None, False, "http_5xx"),
        (503, None, False, "http_5xx"),
        (401, None, False, "unexpected_status"),
        (403, None, False, "unexpected_status"),
        (400, None, False, "unexpected_status"),
        (None, httpx.ConnectTimeout("x"), False, "timeout"),
        (None, httpx.ReadTimeout("x"), False, "timeout"),
        (None, httpx.ConnectError("x"), False, "connection_error"),
        (None, httpx.ReadError("x"), False, "connection_error"),
        (None, RuntimeError("x"), False, "unknown_error"),
    ],
)
def test_classify_health(
    status_code, exc, expected_healthy, expected_reason
):
    verdict = classify_health(status_code, exc=exc)
    assert verdict.healthy is expected_healthy
    assert verdict.reason == expected_reason
    assert verdict.status_code == status_code
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/liyifan/llm_gateway && python -m pytest tests/test_health_checker.py::test_classify_health -v`
Expected: FAIL — `ImportError: cannot import name 'HealthVerdict'`（模块还没建）

- [ ] **Step 3: 写最小实现 `src/llm_gateway/services/health_checker.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

import httpx


HEALTHY_STATUSES = frozenset({200, 404})


@dataclass(frozen=True)
class HealthVerdict:
    healthy: bool
    status_code: int | None
    reason: str


def classify_health(
    status_code: int | None, *, exc: Exception | None
) -> HealthVerdict:
    """Classify an upstream /models probe into a health verdict.

    200/404 are healthy (404 = 昇腾 PD 分离查不到 /models，明确是健康的).
    Any 5xx, network error, timeout, or non-404 4xx is unhealthy and triggers
    automatic disable.
    """
    if exc is not None:
        if isinstance(exc, httpx.TimeoutException):
            return HealthVerdict(False, None, "timeout")
        if isinstance(exc, httpx.HTTPError):
            return HealthVerdict(False, None, "connection_error")
        return HealthVerdict(False, None, "unknown_error")
    if status_code in HEALTHY_STATUSES:
        return HealthVerdict(True, status_code, "ok")
    if status_code >= 500:
        return HealthVerdict(False, status_code, "http_5xx")
    return HealthVerdict(False, status_code, "unexpected_status")
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/liyifan/llm_gateway && python -m pytest tests/test_health_checker.py::test_classify_health -v`
Expected: PASS（13 个参数化用例全过）

- [ ] **Step 5: Commit**

```bash
git add src/llm_gateway/services/health_checker.py tests/test_health_checker.py
git commit -m "Add classify_health verdict function for upstream probes"
```

---

## Task 3: 探测单个 upstream

**Files:**
- Modify: `src/llm_gateway/services/health_checker.py`（追加 `_probe_upstream`）
- Test: `tests/test_health_checker.py`（追加测试）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_health_checker.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_probe_upstream_returns_ok_verdict_for_200(monkeypatch):
    from llm_gateway.services import health_checker

    upstream = _FakeUpstream(base_url="http://upstream.local", health_path="/models")

    class _FakeResponse:
        status_code = 200

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, *args, **kwargs):
            return _FakeResponse()

    monkeypatch.setattr(health_checker.httpx, "AsyncClient", _FakeClient)

    verdict = await health_checker._probe_upstream(upstream, timeout_seconds=3.0)
    assert verdict == health_checker.HealthVerdict(
        healthy=True, status_code=200, reason="ok"
    )


@pytest.mark.asyncio
async def test_probe_upstream_returns_http_5xx_verdict(monkeypatch):
    from llm_gateway.services import health_checker

    upstream = _FakeUpstream(base_url="http://upstream.local", health_path="/models")

    class _FakeResponse:
        status_code = 500

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, *args, **kwargs):
            return _FakeResponse()

    monkeypatch.setattr(health_checker.httpx, "AsyncClient", _FakeClient)

    verdict = await health_checker._probe_upstream(upstream, timeout_seconds=3.0)
    assert verdict == health_checker.HealthVerdict(
        healthy=False, status_code=500, reason="http_5xx"
    )


@pytest.mark.asyncio
async def test_probe_upstream_returns_timeout_verdict(monkeypatch):
    from llm_gateway.services import health_checker

    upstream = _FakeUpstream(base_url="http://upstream.local", health_path="/models")

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, *args, **kwargs):
            raise health_checker.httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(health_checker.httpx, "AsyncClient", _FakeClient)

    verdict = await health_checker._probe_upstream(upstream, timeout_seconds=3.0)
    assert verdict == health_checker.HealthVerdict(
        healthy=False, status_code=None, reason="timeout"
    )


class _FakeUpstream:
    """Stand-in for UpstreamTarget with the fields _probe_upstream reads."""

    def __init__(
        self,
        *,
        base_url: str,
        health_path: str = "/models",
        api_key_value: str | None = None,
        api_key_ref: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        self.base_url = base_url
        self.health_path = health_path
        self.api_key_value = api_key_value
        self.api_key_ref = api_key_ref
        self.extra_headers = extra_headers or {}
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/liyifan/llm_gateway && python -m pytest tests/test_health_checker.py -k probe_upstream -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_probe_upstream'`

- [ ] **Step 3: 实现 `_probe_upstream`**

在 `src/llm_gateway/services/health_checker.py` 末尾追加：

```python
from urllib.parse import urljoin


async def _probe_upstream(upstream, *, timeout_seconds: float) -> HealthVerdict:
    """GET {base_url}/{health_path} and classify the response.

    Mirrors the request construction of litellm_client.check_upstream_health
    (same base_url join, same header injection) but applies the stricter
    classify_health verdict used by the background checker.
    """
    # base_url 形如 "http://host:port/v1"，health_path 形如 "/models"。
    # 用字符串拼接保留与 check_upstream_health 完全一致的 URL 形态。
    url = upstream.base_url.rstrip("/") + "/" + upstream.health_path.lstrip("/")
    headers = dict(upstream.extra_headers or {})
    api_key = upstream.api_key_value or upstream.api_key_ref
    if api_key:
        headers.setdefault("Authorization", f"Bearer {api_key}")
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(url, headers=headers)
        return classify_health(response.status_code, exc=None)
    except Exception as exc:
        return classify_health(None, exc=exc)
```

注意：`urljoin` 这行 import 放文件顶部更合适——见 Step 4 修正。

- [ ] **Step 4: 把 import 提到文件顶部**

实际我们没用到 `urljoin`（用的是字符串拼接，与 `check_upstream_health` 一致）。删掉 Step 3 里多余的 `from urllib.parse import urljoin` 这一行，保持文件干净。

- [ ] **Step 5: 运行测试验证通过**

Run: `cd /Users/liyifan/llm_gateway && python -m pytest tests/test_health_checker.py -k probe_upstream -v`
Expected: PASS（3 个探测测试全过）

- [ ] **Step 6: Commit**

```bash
git add src/llm_gateway/services/health_checker.py tests/test_health_checker.py
git commit -m "Add _probe_upstream to GET /models and classify health"
```

---

## Task 4: 禁用动作（含双重确认 + 审计）

**Files:**
- Modify: `src/llm_gateway/services/health_checker.py`（追加 `_disable_upstream`）
- Test: `tests/test_health_checker.py`（追加测试）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_health_checker.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_disable_upstream_sets_state_and_writes_audit(monkeypatch):
    """探测失败 → 双重确认仍 ACTIVE → 写 DISABLED + audit event。"""
    from llm_gateway.db.models import ResourceState
    from llm_gateway.services import health_checker

    upstream = _FakePersistedUpstream(state=ResourceState.ACTIVE)
    sessions = []

    monkeypatch.setattr(
        "llm_gateway.services.health_checker.AsyncSessionLocal",
        _make_fake_session_local(sessions, upstream),
    )
    recorded = []
    monkeypatch.setattr(
        "llm_gateway.services.health_checker.record_audit_event",
        _make_fake_record_audit(recorded),
    )

    verdict = health_checker.HealthVerdict(False, 500, "http_5xx")
    disabled = await health_checker._disable_upstream(
        upstream_id=upstream.id, verdict=verdict
    )

    assert disabled is True
    assert upstream.state == ResourceState.DISABLED
    assert upstream._committed is True
    assert len(recorded) == 1
    assert recorded[0]["action"] == "upstream.auto_disable"
    assert recorded[0]["resource_id"] == upstream.id
    assert recorded[0]["outcome"] == "disabled"
    assert recorded[0]["detail"]["verdict"] == "http_5xx"
    assert recorded[0]["detail"]["status_code"] == 500


@pytest.mark.asyncio
async def test_disable_upstream_skips_when_already_disabled(monkeypatch):
    """双重确认：探测后管理员已手动禁用 → 不重复写 audit。"""
    from llm_gateway.db.models import ResourceState
    from llm_gateway.services import health_checker

    upstream = _FakePersistedUpstream(state=ResourceState.DISABLED)
    sessions = []
    monkeypatch.setattr(
        "llm_gateway.services.health_checker.AsyncSessionLocal",
        _make_fake_session_local(sessions, upstream),
    )
    recorded = []
    monkeypatch.setattr(
        "llm_gateway.services.health_checker.record_audit_event",
        _make_fake_record_audit(recorded),
    )

    verdict = health_checker.HealthVerdict(False, 500, "http_5xx")
    disabled = await health_checker._disable_upstream(
        upstream_id=upstream.id, verdict=verdict
    )

    assert disabled is False
    assert len(recorded) == 0  # 不重复审计


class _FakePersistedUpstream:
    def __init__(self, *, state):
        from uuid import uuid4

        self.id = uuid4()
        self.name = "fake-upstream"
        self.health_path = "/models"
        self.state = state
        self._committed = False


def _make_fake_session_local(sessions, upstream):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_session_local():
        class _Session:
            async def get(self_inner, model, pk):
                return upstream

            async def commit(self_inner):
                upstream._committed = True

        s = _Session()
        sessions.append(s)
        try:
            yield s
        finally:
            pass

    return _fake_session_local


def _make_fake_record_audit(recorded):
    async def _fake_record(session, **kwargs):
        recorded.append(kwargs)
        return None

    return _fake_record
```

- [ ] **Step 2: 实现 `_disable_upstream`**

在 `src/llm_gateway/services/health_checker.py`：
- 文件顶部 import 区追加：

```python
from sqlalchemy import select
from sqlmodel import col

from llm_gateway.db.models import ResourceState, UpstreamTarget
from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.services.facts import record_audit_event
```

- 文件末尾追加：

```python
async def _disable_upstream(*, upstream_id, verdict: HealthVerdict) -> bool:
    """Set an ACTIVE upstream to DISABLED after a failed probe.

    Double-confirm state on read: if an admin disabled or restored the upstream
    between probe and commit, do nothing (no state overwrite, no duplicate
    audit). Returns True when the disable was applied.
    """
    async with AsyncSessionLocal() as session:
        upstream = await session.get(UpstreamTarget, upstream_id)
        if upstream is None or upstream.state != ResourceState.ACTIVE:
            return False
        upstream.state = ResourceState.DISABLED
        await record_audit_event(
            session,
            action="upstream.auto_disable",
            resource_type="upstream_target",
            resource_id=upstream.id,
            outcome="disabled",
            detail={
                "name": upstream.name,
                "health_path": upstream.health_path,
                "verdict": verdict.reason,
                "status_code": verdict.status_code,
            },
        )
        await session.commit()
        return True
```

- [ ] **Step 3: 运行测试验证通过**

Run: `cd /Users/liyifan/llm_gateway && python -m pytest tests/test_health_checker.py -k disable_upstream -v`
Expected: PASS（2 个禁用测试全过）

- [ ] **Step 4: Commit**

```bash
git add src/llm_gateway/services/health_checker.py tests/test_health_checker.py
git commit -m "Add _disable_upstream with double-confirm and audit event"
```

---

## Task 5: 巡检循环主体

**Files:**
- Modify: `src/llm_gateway/services/health_checker.py`（追加 `_run_once` 和 `_collect_active_upstreams`）
- Test: `tests/test_health_checker.py`（追加测试）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_health_checker.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_run_once_disables_failing_upstream_and_leaves_healthy(monkeypatch):
    """一轮巡检：一个挂、一个正常 → 只禁挂的，正常的完好。"""
    from llm_gateway.db.models import ResourceState
    from llm_gateway.services import health_checker

    bad = _FakePersistedUpstream(state=ResourceState.ACTIVE)
    bad.name = "bad-upstream"
    good = _FakePersistedUpstream(state=ResourceState.ACTIVE)
    good.name = "good-upstream"
    active = [bad, good]

    async def _fake_collect(session):
        return list(active)

    monkeypatch.setattr(health_checker, "_collect_active_upstreams", _fake_collect)

    probe_calls = []

    async def _fake_probe(upstream, *, timeout_seconds):
        probe_calls.append(upstream.name)
        if upstream is bad:
            return health_checker.HealthVerdict(False, 500, "http_5xx")
        return health_checker.HealthVerdict(True, 200, "ok")

    monkeypatch.setattr(health_checker, "_probe_upstream", _fake_probe)

    disabled = []

    async def _fake_disable(*, upstream_id, verdict):
        for item in active:
            if item.id == upstream_id:
                item.state = ResourceState.DISABLED
                disabled.append(upstream_id)
                return True
        return False

    monkeypatch.setattr(health_checker, "_disable_upstream", _fake_disable)

    await health_checker._run_once(
        timeout_seconds=3.0,
    )

    assert sorted(probe_calls) == ["bad-upstream", "good-upstream"]
    assert bad.state == ResourceState.DISABLED
    assert good.state == ResourceState.ACTIVE
    assert disabled == [bad.id]


@pytest.mark.asyncio
async def test_run_once_ignores_404_as_healthy(monkeypatch):
    """404（昇腾 PD 分离）应被视为健康，不禁用。"""
    from llm_gateway.db.models import ResourceState
    from llm_gateway.services import health_checker

    pd = _FakePersistedUpstream(state=ResourceState.ACTIVE)
    active = [pd]

    async def _fake_collect(session):
        return list(active)

    monkeypatch.setattr(health_checker, "_collect_active_upstreams", _fake_collect)

    async def _fake_probe(upstream, *, timeout_seconds):
        return health_checker.HealthVerdict(True, 404, "ok")

    monkeypatch.setattr(health_checker, "_probe_upstream", _fake_probe)

    disabled = []

    async def _fake_disable(*, upstream_id, verdict):
        disabled.append(upstream_id)
        return True

    monkeypatch.setattr(health_checker, "_disable_upstream", _fake_disable)

    await health_checker._run_once(timeout_seconds=3.0)

    assert pd.state == ResourceState.ACTIVE
    assert disabled == []
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/liyifan/llm_gateway && python -m pytest tests/test_health_checker.py -k run_once -v`
Expected: FAIL — `AttributeError: ... has no attribute '_run_once'`

- [ ] **Step 3: 实现 `_collect_active_upstreams` 和 `_run_once`**

在 `src/llm_gateway/services/health_checker.py` 末尾追加：

```python
async def _collect_active_upstreams(session) -> list:
    result = await session.execute(
        select(UpstreamTarget).where(col(UpstreamTarget.state) == ResourceState.ACTIVE)
    )
    return list(result.scalars().all())


async def _run_once(*, timeout_seconds: float) -> None:
    """Probe every ACTIVE upstream concurrently; disable the failures.

    Probes run concurrently (asyncio.gather) so N replicas finish within one
    timeout window. Disables are serial with per-upstream sessions so one
    failure cannot poison another.
    """
    async with AsyncSessionLocal() as session:
        upstreams = await _collect_active_upstreams(session)

    if not upstreams:
        return

    verdicts = await asyncio.gather(
        *[_probe_upstream(upstream, timeout_seconds=timeout_seconds) for upstream in upstreams]
    )

    for upstream, verdict in zip(upstreams, verdicts, strict=True):
        if verdict.healthy:
            continue
        try:
            await _disable_upstream(upstream_id=upstream.id, verdict=verdict)
        except Exception:
            logger.exception(
                "health_check_disable_failed upstream_id=%s", upstream.id
            )
```

同时在文件顶部 import 区补：

```python
import asyncio
import logging
```

并在 import 之后、`HEALTHY_STATUSES` 之前加：

```python
logger = logging.getLogger(__name__)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/liyifan/llm_gateway && python -m pytest tests/test_health_checker.py -k run_once -v`
Expected: PASS（2 个 run_once 测试全过）

- [ ] **Step 5: Commit**

```bash
git add src/llm_gateway/services/health_checker.py tests/test_health_checker.py
git commit -m "Add health check run_once loop (concurrent probe, serial disable)"
```

---

## Task 6: 生命周期（start/stop）

**Files:**
- Modify: `src/llm_gateway/services/health_checker.py`（追加 `start`/`stop`）
- Test: `tests/test_health_checker.py`（追加测试）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_health_checker.py` 末尾追加。注意 `start()` 的语义是 **fire-and-forget**：启动后台 task 后立即返回，真正的循环在 `_main_loop()` 里。下面的测试按此语义编写。

```python
@pytest.mark.asyncio
async def test_start_skips_loop_when_disabled(monkeypatch):
    """health_check_enabled=False → start() 不进入循环。"""
    from llm_gateway.services import health_checker

    monkeypatch.setattr(health_checker, "_settings_enabled", lambda: False)
    loop_calls = []

    async def _fake_loop():
        loop_calls.append("ran")

    monkeypatch.setattr(health_checker, "_main_loop", _fake_loop)

    await health_checker.start()
    assert loop_calls == []  # 循环没跑
    assert health_checker._task is None


@pytest.mark.asyncio
async def test_start_runs_loop_then_stop_terminates(monkeypatch):
    """start() 起后台 task 并立即返回；stop() 取消并等待其退出。"""
    import asyncio as _asyncio

    from llm_gateway.services import health_checker

    monkeypatch.setattr(health_checker, "_settings_enabled", lambda: True)
    monkeypatch.setattr(health_checker, "_settings_interval", lambda: 0.01)
    monkeypatch.setattr(health_checker, "_settings_timeout", lambda: 3.0)

    iterations = []

    async def _fake_run_once(*, timeout_seconds):
        iterations.append(1)

    monkeypatch.setattr(health_checker, "_run_once", _fake_run_once)

    await health_checker.start()
    assert health_checker._task is not None  # 后台 task 已起
    # 让循环跑几轮
    await _asyncio.sleep(0.05)
    await health_checker.stop()
    assert health_checker._task is None  # 已停止
    assert len(iterations) >= 1
```

- [ ] **Step 2: 实现 `start`/`stop`/`_main_loop` + settings 访问器**

在 `src/llm_gateway/services/health_checker.py` 末尾追加：

```python
def _settings_enabled() -> bool:
    from llm_gateway.core.config import get_settings

    return get_settings().health_check_enabled


def _settings_interval() -> float:
    from llm_gateway.core.config import get_settings

    return get_settings().health_check_interval_seconds


def _settings_timeout() -> float:
    from llm_gateway.core.config import get_settings

    return get_settings().health_check_timeout_seconds


_task: asyncio.Task | None = None


async def start() -> None:
    """Start the background health-check loop (no-op if disabled).

    Fire-and-forget: schedules _main_loop and returns immediately. Idempotent —
    a second call while a task is running is ignored.
    """
    global _task
    if _task is not None:
        return
    if not _settings_enabled():
        logger.info("health_check_disabled_by_config")
        return
    _task = asyncio.create_task(_main_loop())
    logger.info(
        "health_check_started interval=%.1fs timeout=%.1fs",
        _settings_interval(),
        _settings_timeout(),
    )


async def stop() -> None:
    """Cancel the background loop and wait for it to wind down.

    Safe to call when no task is running. Used by lifespan shutdown so a restart
    never leaves a dangling task probing /models.
    """
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("health_check_loop_error")
    _task = None
    logger.info("health_check_stopped")


async def _main_loop() -> None:
    """Run _run_once every interval until cancelled."""
    timeout = _settings_timeout()
    while True:
        try:
            await _run_once(timeout_seconds=timeout)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("health_check_iteration_failed")
        await asyncio.sleep(_settings_interval())
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/liyifan/llm_gateway && python -m pytest tests/test_health_checker.py -k "start or stop" -v`
Expected: PASS（2 个生命周期测试全过）

- [ ] **Step 5: Commit**

```bash
git add src/llm_gateway/services/health_checker.py tests/test_health_checker.py
git commit -m "Add health_checker start/stop lifecycle with config-gated loop"
```

---

## Task 7: 接入 lifespan

**Files:**
- Modify: `src/llm_gateway/main.py`（lifespan 函数）

- [ ] **Step 1: 阅读现有 lifespan**

Run: `cd /Users/liyifan/llm_gateway && sed -n '35,56p' src/llm_gateway/main.py`
确认 `lifespan` 函数当前结构（startup 做 `ensure_builtin_identity` / `litellm.request_timeout` / `init_analytics`；shutdown 做 `drain_now()` / `close_analytics()`）。

- [ ] **Step 2: 修改 lifespan**

在 `src/llm_gateway/main.py`：

(a) 文件顶部 import 区，在 `from llm_gateway.services.facts_queue import drain_now` 之后追加：

```python
from llm_gateway.services import health_checker
```

(b) 在 lifespan 函数里，`init_analytics(settings)` 这一行之后、`yield` 之前追加：

```python
        await health_checker.start()
```

(c) 在 `yield` 之后、`await drain_now()` 之前追加：

```python
        await health_checker.stop()
```

修改后的 lifespan 片段应为：

```python
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncSessionLocal() as session:
            await ensure_builtin_identity(session, settings)
            await session.commit()
        _guard_default_admin_credentials(settings)
        litellm.request_timeout = settings.upstream_timeout_seconds
        init_analytics(settings)
        await health_checker.start()
        yield
        await health_checker.stop()
        await drain_now()
        close_analytics()
```

- [ ] **Step 3: 冒烟测试——app 能起停且巡检 task 正确起停**

Run: `cd /Users/liyifan/llm_gateway && python -c "
import asyncio
from llm_gateway.main import app
from contextlib import asynccontextmanager

async def smoke():
    from llm_gateway.services import health_checker
    # 模拟 lifespan: app 启动会触发 start
    async with app.router.lifespan_context(app):
        assert health_checker._task is not None
        print('task started:', health_checker._task)
    # lifespan 退出后 task 应被 stop
    assert health_checker._task is None
    print('task stopped after lifespan exit')

asyncio.run(smoke())
"`
Expected:
```
task started: <Task ...>
task stopped after lifespan exit
```

- [ ] **Step 4: Commit**

```bash
git add src/llm_gateway/main.py
git commit -m "Wire health_checker into app lifespan (start on startup, stop on shutdown)"
```

---

## Task 8: 全量回归 + 文档更新

**Files:**
- Run: 全量测试套件
- Modify: `README.md`（若提及 MVP boundaries / 后台任务，补一句健康巡检）

- [ ] **Step 1: 跑全量测试**

Run: `cd /Users/liyifan/llm_gateway && python -m pytest tests/ -v --timeout=120 2>&1 | tail -40`
Expected: 全绿。重点关注：
- `tests/test_health_checker.py` 全部新测试通过
- 原有测试（尤其 `test_facts_queue.py`、`test_proxy_*.py`、`test_audit_*.py`）无回归

若有 backend integration 测试需要真实 upstream，可能需要环境变量；若该类测试在 CI 外常跳过，记录下来即可。

- [ ] **Step 2: 检查 README 是否需要补充**

Run: `cd /Users/liyifan/llm_gateway && grep -n -i "health\|lifespan\|background\|mvp boundar" README.md`

如果 README 里有"Current MVP Boundaries"或列出后台任务的段落，追加一行说明健康巡检已加入。如果没有相关段落，跳过本步。

- [ ] **Step 3: 最终 Commit（若有 README/docs 改动）**

```bash
git add README.md
git commit -m "Document upstream health check auto-disable in README"
```

若 README 无需改动，本步跳过。

- [ ] **Step 4: 推送前自检**

Run: `cd /Users/liyifan/llm_gateway && git log --oneline -10`
确认提交链清晰：
1. Add health check settings
2. Add classify_health
3. Add _probe_upstream
4. Add _disable_upstream
5. Add _run_once loop
6. Add start/stop lifecycle
7. Wire health_checker into lifespan
8. (可选) Document in README

---

## Self-Review Checklist（实施前已自查）

**Spec 覆盖：**
- ✅ 后台 3s 巡检循环 → Task 5 `_run_once` + Task 6 `_main_loop`
- ✅ 健康判定（200/404 健康，5xx/网络/超时/4xx 禁用）→ Task 2 `classify_health`
- ✅ 1 次失败立即禁用 → Task 5 `_run_once` 单次探测失败即调 `_disable_upstream`
- ✅ 写 `upstream.auto_disable` 审计 → Task 4 `_disable_upstream`
- ✅ 纯手动恢复（巡检不复探 DISABLED）→ Task 5 `_collect_active_upstreams` 只查 ACTIVE
- ✅ 双重确认防竞态 → Task 4 `_disable_upstream` 重新读 state
- ✅ 复用 state 字段，无 DB 迁移 → 全程不动 models.py
- ✅ 配置化 interval/timeout/enabled → Task 1 + Task 6 settings 访问器
- ✅ lifespan 挂载 → Task 7
- ✅ 测试矩阵（纯函数 + 循环 + 竞态）→ Task 2/3/4/5/6

**已知窄窗口竞态**（spec 已记录，接受）：探测失败后、管理员恢复后的 <50ms 内，巡检可能覆盖一次恢复，管理员重试可恢复。不引入版本号。
