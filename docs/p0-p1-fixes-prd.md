# P0/P1 修复 PRD

- 状态: 执行中
- 基线评审: 本会话前半段的代码评审(数据面/安全/持久化/前端/测试五份深读报告)
- 目标: 把评审里的 P0 全部、P1 全部(含纯安全加固 + 大型结构重构)修复,全量回测绿后推送。

## 原则
- 每个 P0/P1 都已逐条在代码里核实存在(非误报),修复针对真实根因。
- 不改对外协议行为;数据库变更走 Alembic 可逆迁移。
- 每个修复带回归测试;全量后端 pytest + 前端 check/test 绿才算完成。
- 不做自我背书:写完后由独立验证环节复核。

---

## P0

### P0-1 鉴权缓存撤销窗口 → 版本化 cache key
- 现状: `security.py` auth_cache、`policy.py`/`rate_limit.py` policy_cache,TTL=30s,业务代码零 `invalidate()`。disable key / 移除 team / 删 entitlement 后最长 30s 仍放行。
- 方案: cache key 拼上主体版本号。auth_cache key = `f"{key_hash}:{key.updated_at_ts}:{subject.updated_at_ts}:{project.updated_at_ts}"`,任一状态变更(`updated_at` 跳变)天然失效,无需手动 invalidate。policy_cache(模型访问)同理拼 `subject.updated_at` + 关键 grant 的 `updated_at`。这是根治,优于补 invalidate 清单(后者注定再漏)。
- 涉及: `services/security.py`、`services/policy.py`、`services/cache.py`。
- 测试: 新增"disable key → 同 key 下一次请求立即 401""移除 team membership → 立即 403"两条回归。

### P0-2 facts_queue 关停丢数据 + 批处理隔离
- 现状: `facts_queue.py` `drain_now()` 从不被调用(shutdown 丢未落盘 fact);单 session 循环 add+flush,一条坏 fact 毒化 session 致整批丢;batch 已 clear 无法恢复。
- 方案: ① `main.py` lifespan shutdown 段 `await drain_now()`(带超时循环到空)。② `_drain` 改为 per-fact 独立事务:每条 `record_request_fact` 用独立 session/commit,单条失败仅丢该条并记日志,不污染其他。③ commit 级失败时把 batch 还原回 `_pending` 队首,下次重试。
- 涉及: `services/facts_queue.py`、`main.py`。
- 测试: 新增"enqueue 后 await drain_now → 全部落库""一条坏 fact 不影响其他 fact"。

### P0-3 审计日志脱敏上游 key
- 现状: `admin.py:1347` `_audit_update` 对含 `api_key_value` 的 UpstreamTargetUpdate 做 `model_dump(exclude_unset=True)` 无脱敏,轮换上游 key 时明文入 `audit_events.detail`。
- 方案: `record_audit_event` / `_audit_update` 写入前 scrub 敏感字段(`api_key_value`、`password`、`api_key_ref`)。给 `_audit_update` 加 `redact_fields` 默认集合;subject 已 exclude password,统一收拢。
- 涉及: `api/admin.py`、`services/facts.py`(record_audit_event)、`services/resource_payloads.py`。
- 测试: 新增"PATCH upstream 带 api_key_value → audit detail 不含明文"。

### P0-4 默认 admin 凭据守卫 + 常量时间比较
- 现状: 默认 `admin_token="dev-admin-token"`、`bootstrap_admin_password="dev-admin-password"`;`deps.py:99`/`realtime.py:93` 用 `!=`/`==` 比较;`security.py:340` 空密码会重置回默认。
- 方案: ① admin token 比较改 `hmac.compare_digest`。② 新增 `LLM_GATEWAY_REQUIRE_NONDEFAULT_ADMIN_CREDENTIALS`(默认 `environment != "local"` 时为 True),启动时若仍是默认值则拒绝启动并高优日志。③ 移除"空 password_hash 重置回默认密码"逻辑,改为空密码即拒绝登录。
- 涉及: `api/deps.py`、`api/realtime.py`、`services/security.py`、`core/config.py`、`main.py`。
- 测试: 新增"compare_digest 路径""非 local 且默认凭据 → 启动失败"。

### P0-5 rate_limit_fail_closed 语义代码化
- 现状: 配置存在但零读取点;Redis 故障靠异常冒泡成 500(巧合 fail-closed)。
- 方案: `check_request_rate`/`acquire_concurrency_slot` 内捕获 `RedisError`,按 `settings.rate_limit_fail_closed` 决定 raise `RateLimitExceeded`(fail-closed)或放行(fail-open)。
- 涉及: `services/rate_limit.py`。
- 测试: 新增"Redis 故障 + fail_closed=True → 429""fail_closed=False → 放行"。

---

## P1

### P1-6 DuckDB 重查询隔离 + statement_timeout
- 现状: 单连接 + threading.Lock 串行;经 postgres_scanner 直打主库,无超时。
- 方案: ① ATTACH 后 `SET statement_timeout`(可配,默认 15s)防失控查询。② 新增 `LLM_GATEWAY_ANALYTICS_DATABASE_URL`(可选,默认=主库 URL),允许指向只读副本;文档化。③ 连接 Lock 保持(单连接语义不变)。
- 涉及: `services/duckdb_analytics.py`、`core/config.py`。

### P1-7 session.py 连接池显式配置
- 现状: 仅 `pool_pre_ping`,默认 pool_size=5+overflow=10。
- 方案: `create_async_engine(..., pool_size=, max_overflow=, pool_timeout=, pool_recycle=)`,值来自新增可配 settings(默认 pool_size=20, max_overflow=40, pool_recycle=1800)。
- 涉及: `db/session.py`、`core/config.py`。

### P1-8 proxy.py 三协议去重
- 现状: `openai_chat_completions`/`openai_responses`/`anthropic_messages` 与三个 `_stream_*` 近乎逐行复制。
- 方案: 抽出 `_proxy_endpoint(endpoint_family, request, redis, settings, client_ip)` + `_stream_endpoint(endpoint_family, ...)` 统一处理非流/流/限流/记账/sticky,三个路由变为一行委托。
- 涉及: `api/proxy.py`。
- 风险控制: 保持协议行为逐字节不变;靠现有 72 测试 + 流式回归守护。

### P1-9 登录失败限流 + 防用户名枚举
- 现状: `/auth/login` 无限流;用户不存在路径跳过 PBKDF2 → 时序差可枚举。
- 方案: ① login 失败路径恒定执行一次 dummy `verify_password`(对固定 dummy hash),消除时序差。② `/auth/login`、`/auth/register` 加按 IP + 按用户名的失败计数限流(复用 Redis)。
- 涉及: `api/auth.py`、`services/security.py`、`services/rate_limit.py`。
- 测试: 新增"不存在用户与错误密码耗时一致""连续失败触发限流"。

### P1-10 FK ON DELETE 策略(Alembic 迁移)→ ⚠️ 待审草案,本轮不自动落地
- 现状: 几乎所有 FK 裸 `NO ACTION`,引用完整性靠应用层枚举。
- **本轮不执行的理由(负责任的取舍)**: 测试 fixture `migrated_database` 在每次跑测试时对 `.env.local` 配置的数据库执行 `alembic upgrade head`。一旦把 FK ALTER 迁移推进到 head,下一次测试/部署就会对可能含有大量 `request_facts` 的库做 FK 重建(全表校验 + 短暂 ACCESS EXCLUSIVE 锁)——这是不易逆的 schema 变更,应在确认目标 DB 后单独、显式执行,而非随大批改动一起自动推送。
- 当前应用层级联(`delete_subject` 等)仍正确工作且有测试守护,所以这是"加固/清债"而非活 bug,推迟不影响正确性。
- **待审草案**(确认 DB 后再实现为新迁移 `20260615_0009_fk_on_delete.py`):
  - `request_facts` 的 `subject_id`/`project_id`/`upstream_target_id`、`audit_events.actor_subject_id` → `ON DELETE SET NULL`(保留历史事实/审计)。
  - 从属配置表(`team_memberships`/`user_sessions`/`model_entitlements`/`model_team_grants`/`gateway_keys` 等)→ `ON DELETE CASCADE`。
  - 实现:`op.drop_constraint("<table>_<col>_fkey", "<table>")` 后 `op.create_foreign_key(..., ondelete="SET NULL"/"CASCADE")`,并对大表用 `NOT VALID` + 后台 `VALIDATE CONSTRAINT` 避免长锁。
  - 同步更新 `db/models.py` 的 FK 声明加 `ondelete=`,并简化 `admin.py` 的 `delete_subject` 手动级联。

### P1-11 前端 +page.svelte 拆分
- 现状: 1932 行 / 227 符号单组件,塞登录/注册/dashboard/admin 12 section/教程。
- 方案: 按 SvelteKit 路由拆:`routes/+page.svelte` 仅 auth gate + 分发;admin 各 section 抽组件(`lib/components/admin/*.svelte`);labels/store 抽 `lib/stores/`。复用现有 snippet 雏形。保持行为不变,17 vitest + e2e 绿。
- 涉及: `frontend/src/routes/+page.svelte`、新增组件/store、`frontend/src/routes/+layout.svelte`。

### P1-12 审计事件补齐 actor
- 现状: admin_dep 返回 None,所有 admin 审计 `actor_subject_id` 恒 NULL。
- 方案: `admin_dep` 返回 `AdminActorContext`(区分 token-based 系统 vs session-based 真人,带 subject_id);所有 `record_audit_event` 调用传 `actor_subject_id`。
- 涉及: `api/deps.py`、`api/admin.py`(所有 record_audit_event 点)。

### P1-13 admin.py 按资源组拆分(评审 M1)
- 现状: 1349 行/143 符号单文件。
- 方案: 拆 `api/admin/` 子包(identity/routing/observability/policy),共享 helper 下沉 `admin/_common.py`,兼容路由前缀不变。
- 涉及: `api/admin.py` → `api/admin/*.py`。

---

## 执行波次
- **Wave A(后端可靠性/安全,定向)**:P0-1/2/3/4/5 + P1-6/7/9/12。多为独立文件,可并行 + 我亲自控正确性。
- **Wave B(后端结构重构)**:P1-8(proxy 去重)、P1-13(admin 拆分)。A 绿后做,靠测试守护。
- **Wave C(前端重构)**:P1-11。独立于后端,可作大背景 agent 并行。
- **Wave D(迁移)**:P1-10。独立,A/B 绿后跑 `init_db.py` 验证。

## 完成判据
- `uv run pytest -q` 全绿(含新增回归)。
- `cd frontend && npm run check && npm run test` 全绿。
- 无回归:现有 72 后端 + 17 前端测试不退化。
- 推送特性分支 `hardening/p0-p1-fixes` 到 GitHub(不直接 force main)。
