# Upstream 健康巡检与自动禁用

**日期**: 2026-06-29
**状态**: 已确认，待实现

## 背景与目标

当前多上游负载均衡只在路由选择时被动读取 vLLM `/metrics`（Redis 缓存 3s）做负载感知，**没有任何主动健康巡检**。一个 vLLM 副本返回 500 或彻底死掉时，它仍会继续接收新请求，直到管理员手动发现并禁用。

本设计增加一个**后台健康巡检循环**，周期性探测每个 ACTIVE upstream 的 `/models` 端点，一旦发现故障就自动把它设为 DISABLED，从而被路由层摘除。管理员重启节点后手动恢复。

## 设计原则

**自动禁用 = 手动禁用的效果，只是触发者从人变成后台任务。** 两者都把 `UpstreamTarget.state` 设成 DISABLED，路由层一视同仁地过滤。区别只记录在审计日志的 `action` 字段。

## 范围内

- 后台定时巡检所有 ACTIVE upstream 的 `/models`
- 故障自动禁用（state → DISABLED），写审计事件
- 复用现有 admin PATCH 接口做手动恢复
- 复用现有 `state == ACTIVE` 过滤链路（路由 / realtime / 前端零改动）

## 范围外

- **自动恢复**：不做。被自动禁用的节点由巡检永不复探，只有管理员 PATCH `state=active` 才能恢复
- **异构 fallback**：不引入跨 provider/跨模型名的路由
- **前端改动**：不需要。realtime SSE 流和"上游"页都靠过滤 `state==ACTIVE` 自动反映禁用状态
- **数据库迁移**：不需要。复用现有 `upstream_targets.state` 字段

## 架构

新增 `services/health_checker.py`，作为后台 asyncio task 挂在 app lifespan 上，生命周期管理与现有 `facts_queue` 同范式。

```
lifespan startup
   ├─ ensure_builtin_identity(...)
   ├─ ... 现有初始化
   ├─ health_checker.start()          ← 新增
   └─ yield
lifespan shutdown
   ├─ health_checker.stop()           ← 新增：取消 task，等当前一轮结束
   ├─ drain_now()
   └─ close_analytics()
```

### 巡检主循环

```
while running:
    upstreams = SELECT * FROM upstream_targets WHERE state = 'active'   # 独立 session
    verdicts = 并发探测每个 upstream 的 /models（httpx，3s 超时）
    对每个失败的 upstream（独立 session，逐个 commit）：
        双重确认 + state → DISABLED + 写审计事件
    sleep 3 秒
```

**设计要点：**

1. **每轮独立 DB session**：巡检不复用请求级 session。照 `facts_queue._drain` 范式，每轮用一个 `AsyncSessionLocal()` 查询候选；禁用操作每个 upstream 各开一个 session 独立 commit——单节点禁用失败不影响其他节点。

2. **并发探测、串行禁用**：同一轮探测用 `asyncio.gather` 并发（N 个副本 3s 内全完成）；禁用写 DB 逐个独立 session commit，避免长事务。

3. **只看 ACTIVE 节点**：每轮重查 `state == ACTIVE`。已禁用节点巡检**永不复探**——只有管理员 PATCH 回 active 才重新进入巡检视野（符合纯手动恢复）。

4. **优雅停止**：`stop()` 设置取消标志，等待当前一轮探测结束，避免 shutdown 悬挂。

## 健康判定逻辑

纯函数 `classify_health`，便于单测：

```python
@dataclass(frozen=True)
class HealthVerdict:
    healthy: bool
    status_code: int | None       # None = 网络错误/超时，没拿到响应
    reason: str                    # "ok" / "http_5xx" / "unexpected_status" / "timeout" / "connection_error" / "unknown_error"

HEALTHY_STATUSES = frozenset({200, 404})

def classify_health(status_code: int | None, *, exc: Exception | None) -> HealthVerdict:
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
    # 其他 4xx（401/403 等）—— /models 正常应可达，返回 4xx 说明配错或异常
    return HealthVerdict(False, status_code, "unexpected_status")
```

### 判定规则表

| 情况 | 判定 | 理由 |
|---|---|---|
| status 200 | ✅ 健康 | 正常 |
| status 404 | ✅ 健康 | 昇腾 PD 分离时 `/models` 查不到，明确是健康的 |
| status 5xx (500/502/503) | ❌ 禁用 | 核心需求 |
| 连接错误 / 拒接 | ❌ 禁用 | 节点死了 |
| 超时 (>3s) | ❌ 禁用 | 节点卡死 |
| 其他 4xx (401/403/400 等，除 404) | ❌ 禁用 | `/models` 是无副作用只读端点，正常不应返回 4xx；返回 4xx 说明 api_key 配错或服务异常 |

**保留现有 `check_upstream_health`（admin 手动端点用）**：它的宽松判定 `200 <= code < 500` 适合管理员手动触发的"看看通不通"场景，巡检用更严格的 `classify_health`。两者并存，职责分离。

## 探测请求

```
GET {base_url}/{health_path}        # health_path 默认 "/models"，可逐 upstream 配
Headers:
  Authorization: Bearer {api_key}    # 复用 _api_key(upstream) 逻辑
  ...extra_headers                    # 复用 upstream.extra_headers
Timeout: 3 秒（connect + read 合计）
```

复用现有 `check_upstream_health` 的请求构造方式（同 base_url 拼接、同 header 注入），只换判定函数和超时。

## 配置

`core/config.py` 的 `Settings` 加三个字段（环境变量驱动，沿用现有风格）：

```python
health_check_interval_seconds: float = 3.0     # 巡检间隔
health_check_timeout_seconds: float = 3.0      # 单次探测超时
health_check_enabled: bool = True              # 总开关（调试/排障时可关）
```

遍历范围：每轮 `SELECT * FROM upstream_targets WHERE state = 'active'`。

## 禁用动作与审计

```python
async with AsyncSessionLocal() as session:
    upstream = await session.get(UpstreamTarget, upstream_id)
    # 双重确认：探测到写库之间可能已被管理员手动禁用/恢复
    if upstream is None or upstream.state != ResourceState.ACTIVE:
        return
    upstream.state = ResourceState.DISABLED
    await record_audit_event(
        session,
        action="upstream.auto_disable",        # 区分于手动 upstream.update
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
```

**设计要点：**

1. **审计 action 区分**：`upstream.auto_disable`（巡检自动，`actor_subject_id=NULL`）vs `upstream.update`（管理员手动 PATCH，写自己 subject id）。审计日志一眼可辨。

2. **双重确认（防竞态）**：探测失败 → 开 session → **重新读一次确认还是 ACTIVE** → 才改 DISABLED。避免覆盖管理员在探测后、写库前的手动操作。

3. **detail 字段**：记 `verdict.reason` + `status_code`，管理员在审计日志直接看到"为什么被踢"，不用翻日志。不塞敏感字段。

4. **Sticky 路由清理**：不需要。节点被禁用后，指向它的 sticky 记录还在 Redis，下次请求读到 sticky → 发现不在候选里 → `select_upstream_for_key` 已有的逻辑自动 fallback 到 lowest load。留给 TTL 自然过期。

## 已知竞态（可接受）

简单双重确认存在一个理论窄窗口竞态：

- **窗口**：从探测完成到写库 commit，通常 < 50ms
- **触发条件**：这 50ms 内管理员恰好 PATCH 了同一个 upstream 的 state
- **场景**：探测失败的瞬间管理员恰好恢复（DISABLED → ACTIVE），巡检双重确认读出 ACTIVE → 照样禁用，覆盖了一次手动恢复
- **后果**：管理员的"恢复"被覆盖一次，节点又被禁用
- **可恢复性**：管理员重新 PATCH 一次即可恢复。巡检这轮已跑完，下一轮（3s 后）才会再探

**决策**：接受此窄窗口竞态，不引入乐观锁版本号。理由是窗口极窄（< 50ms）、后果可恢复（管理员重试）、引入版本号复杂度不值得。

## 测试策略

### 纯函数测试（`classify_health`）

| 输入 | 期望 verdict |
|---|---|
| status 200 | healthy=True, reason="ok" |
| status 404 | healthy=True, reason="ok" |
| status 500/502/503 | healthy=False, reason="http_5xx" |
| status 401/403/400 | healthy=False, reason="unexpected_status" |
| `httpx.ConnectTimeout` | healthy=False, reason="timeout" |
| `httpx.ReadTimeout` | healthy=False, reason="timeout" |
| `httpx.ConnectError` | healthy=False, reason="connection_error" |
| `httpx.ReadError` | healthy=False, reason="connection_error" |

### 巡检循环测试

| 场景 | 期望 |
|---|---|
| 一个 upstream 返回 500 | 设为 DISABLED，写一条 `upstream.auto_disable` audit |
| 一个 upstream 超时 | 设为 DISABLED |
| 一个 upstream 返回 200 | 保持 ACTIVE，无 audit |
| 一个 upstream 返回 404 | 保持 ACTIVE（昇腾 PD 分离场景） |
| 两个 upstream，一个挂一个正常 | 只禁用挂的，正常的完好 |
| 已 DISABLED 的 upstream | 巡检不探测、不碰它 |
| 探测失败、写库前管理员已手动禁用 | 不重复写 audit（双重确认：读出非 ACTIVE 跳过） |
| `health_check_enabled=False` | 巡检循环不启动 |

### 整合测试（轻量）

起 app、mock 一个返回 500 的 upstream、等一轮巡检、断言它变 DISABLED。验证 lifespan 集成正确。

## 改动文件清单

| 文件 | 改动 |
|---|---|
| `services/health_checker.py` | **新增**：巡检循环、`classify_health`、`start()`/`stop()` |
| `core/config.py` | 加 3 个 settings 字段 |
| `main.py` lifespan | startup 调 `start()`，shutdown 调 `stop()` |
| `tests/test_health_checker.py` | **新增**：纯函数 + 循环行为测试 |
| 数据库迁移 | **无**（复用现有 `state` 字段） |
| 路由层 / 前端 / realtime | **无改动** |
