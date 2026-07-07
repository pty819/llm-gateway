# 清债重构计划（Wave 0-5）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除审计发现的全部 Critical 项和主要结构性 Important 项，把项目从"迭代做了一半"状态收尾到可维护。

**Architecture:** 六波次渐进重构。Wave 0 应急修复让项目能跑起来；Wave 1 安全收尾；Wave 2 死代码清理；Wave 3 数据模型加固；Wave 4 后端结构重构；Wave 5 前端重构；Wave 6 测试加固。每 task 带回归测试，靠现有测试套件守护行为不变量。

**Tech Stack:** FastAPI + SQLModel + asyncpg + Redis + httpx2 + Python 3.12+；SvelteKit 5 (runes) + TypeScript + Vite；Alembic 迁移。

## Global Constraints

- 不改对外协议行为（HTTP path / 响应体结构 / 状态码不变）
- 数据库变更走可逆 Alembic 迁移，先 upgrade 再 downgrade 双向验证
- 每个修复带回归测试；全量 `uv run pytest -q` + `cd frontend && npm run check && npm run test` 绿才算完成
- 遵循现有分层：controller 只做请求解析/响应，SQL 在 service 层
- Python 最低版本放宽到 `>=3.12`（代码最高用 PEP 604，3.10+ 即可）
- `.env.local` 不动（已 gitignore，未入库）
- 推送特性分支 `cleanup/debt-wave-0-5`，不直接 force main

---

## Wave 0：应急修复 + pre-commit 门禁

### Task 0.1：修复 5 处 Python2 except 语法错误（Critical C1）

**Files:**
- Modify: `src/llm_gateway/services/upstream_routing.py:147,161,174`
- Modify: `src/llm_gateway/services/runtime_metrics.py:385,520`
- Test: `tests/test_import_smoke.py`

**Background:** 这 5 处 `except A, B:` 是 Python2 元组写法，Python3 必须 `except (A, B):`。`ast.parse()` 已确认两文件报 SyntaxError，落在启动导入链上，网关根本起不来。

- [ ] **Step 1: 写 import 冒烟测试**

```python
# tests/test_import_smoke.py
"""Guard against import-time SyntaxError in core modules.

Regression for the Python2-style `except A, B:` syntax errors that broke
the startup import chain. compileall + ast.parse here so the test fails
fast at collection time without needing a live DB.
"""
import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# Modules on the startup import chain — if any fail to parse, the gateway
# cannot start. Listed explicitly so a new SyntaxError surfaces as a named
# test failure rather than an opaque ImportError.
STARTUP_MODULES = [
    "llm_gateway.main",
    "llm_gateway.api.proxy",
    "llm_gateway.services.upstream_routing",
    "llm_gateway.services.runtime_metrics",
    "llm_gateway.services.policy",
]


@pytest.mark.parametrize("module_name", STARTUP_MODULES)
def test_startup_module_parses(module_name: str) -> None:
    """Each startup-path module must be syntactically valid Python 3."""
    # Convert module name to file path relative to src/
    rel = module_name.replace(".", "/") + ".py"
    path = SRC / rel
    source = path.read_text()
    ast.parse(source)  # raises SyntaxError on failure


def test_compileall_src_tree() -> None:
    """Every .py file under src/ must compile."""
    errors = []
    for py in SRC.rglob("*.py"):
        try:
            ast.parse(py.read_text(), filename=str(py))
        except SyntaxError as exc:
            errors.append(f"{py}: {exc}")
    assert not errors, "SyntaxErrors found:\n" + "\n".join(errors)


def test_all_startup_modules_importable() -> None:
    """Importable check (catches import-time errors beyond syntax)."""
    import sys
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    for module_name in STARTUP_MODULES:
        importlib.import_module(module_name)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_import_smoke.py -v`
Expected: FAIL — `test_startup_module_parses[upstream_routing]` and `[runtime_metrics]` fail with SyntaxError; `test_compileall_src_tree` fails listing both files; importable tests error.

- [ ] **Step 3: 修复 upstream_routing.py（3 处）**

行 147: `except TypeError, ValueError, AttributeError:` → `except (TypeError, ValueError, AttributeError):`
行 161: `except TypeError, ValueError:` → `except (TypeError, ValueError):`
行 174: `except TypeError, ValueError:` → `except (TypeError, ValueError):`

- [ ] **Step 4: 修复 runtime_metrics.py（2 处）**

行 385: `except KeyError, TypeError, ValueError:` → `except (KeyError, TypeError, ValueError):`
行 520: `except TypeError, ValueError:` → `except (TypeError, ValueError):`

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_import_smoke.py -v`
Expected: PASS — all 7 cases green

- [ ] **Step 6: 全量 compileall 验证**

Run: `python -m compileall src/ scripts/ -q`
Expected: 无输出（零错误）

- [ ] **Step 7: Commit**

```bash
git add tests/test_import_smoke.py src/llm_gateway/services/upstream_routing.py src/llm_gateway/services/runtime_metrics.py
git commit -m "Fix Python2-style except syntax in upstream_routing and runtime_metrics

5 sites used `except A, B:` (Python2 tuple form) which is a SyntaxError
in Python3. Both files sit on the startup import chain, so the gateway
could not start. Adds tests/test_import_smoke.py as a regression guard
covering ast.parse on the startup modules + full compileall of src/."
```

### Task 0.2：添加 pre-commit 配置（Critical C6）

**Files:**
- Create: `.pre-commit-config.yaml`
- Modify: `pyproject.toml`（dev deps 加 ruff、pre-commit）

**Background:** 项目零 CI、零自动门禁，这正是 C1 的语法错误能潜伏的根因。pre-commit 提供 commit-time 门禁，ruff 覆盖 lint+format，compileall 钩子阻止语法错误入库。

- [ ] **Step 1: 加 ruff 和 pre-commit 到 dev deps**

在 `pyproject.toml` 的 `[dependency-groups] dev` 数组里加 `"ruff>=0.6.0"` 和 `"pre-commit>=4.0.0"`。

- [ ] **Step 2: 写 .pre-commit-config.yaml**

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
        args: [--allow-multiple-documents]
      - id: check-added-large-files
        args: [--maxkb=500]
      - id: check-merge-conflict
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix, --exit-non-zero-on-fix]
      - id: ruff-format
  - repo: local
    hooks:
      - id: python-compileall
        name: python compileall (syntax guard)
        entry: python -m compileall
        language: system
        files: \.py$
        pass_filenames: true
```

- [ ] **Step 3: 安装并运行**

Run: `uv sync && uv run pre-commit install && uv run pre-commit run --all-files`
Expected: 首次运行可能有很多 ruff format 修改（acceptable——把这些一并 commit）；零 SyntaxError；最终 PASS

- [ ] **Step 4: 如有格式修复，单独 commit**

```bash
git add -u
git commit -m "Apply ruff formatting across codebase"
```

- [ ] **Step 5: Commit pre-commit 配置本身**

```bash
git add .pre-commit-config.yaml pyproject.toml uv.lock
git commit -m "Add pre-commit config with ruff and compileall guard

Adds commit-time linting (ruff check + format) and a compileall hook
to prevent SyntaxErrors from entering the tree. Closes the gate that
let the Python2-style except syntax (Task 0.1) reach main."
```

### Task 0.3：放宽 requires-python + 填 description

**Files:**
- Modify: `pyproject.toml:4,6`

**Background:** `requires-python = ">=3.14"` 锁死未稳定的 3.14（2025-10 才正式发布）。代码最高语法是 PEP 604（`str | None`），3.10+ 即可。任何 CI/容器/同事机器装 3.13 都无法安装。`description` 仍是 `uv init` 模板占位符。

- [ ] **Step 1: 修改 pyproject.toml**

```toml
description = "LLM Gateway — proxy, accounting, and admin for OpenAI-compatible upstreams"
requires-python = ">=3.12"
```

- [ ] **Step 2: 重新 lock 并验证**

Run: `uv lock && uv run python -c "import sys; print(sys.version)"`
Expected: lock 成功；Python 版本打印正常

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "Relax requires-python to >=3.12 and fill description

>=3.14 locked out any environment on 3.13 (CI, containers, teammates).
Code uses no 3.14-only features — highest syntax is PEP 604 union types
(3.10+). Fills the uv init placeholder description."
```

---

## Wave 1：安全收尾

### Task 1.1：缓存版本化 key + 显式 invalidate（Critical C2）

**Files:**
- Modify: `src/llm_gateway/services/rate_limit.py:56-95`
- Modify: `src/llm_gateway/services/security.py:103-151`
- Modify: `src/llm_gateway/api/admin/policy.py`（RatePolicy 写路径）
- Test: `tests/test_cache_invalidation.py`

**Interfaces:**
- Produces: `rate_limit.resolve_effective_rate_policy` 的 cache key 格式变为 `rate:{key_id}:{subject_id}:{project_id}:{subject_epoch}`，其中 subject_epoch = subject.updated_at 的 unix timestamp（int）。调用方无需感知。

**Background:** `cache.invalidate()` 全仓零调用。auth_cache 命中后会重新 `_load_auth_context` 校验 state，subject/key 禁用勉强兜得住；但 policy_cache（限流策略）在 admin 改 RatePolicy 后最长 30s 仍按旧策略放行。双保险方案：subject 级变更靠版本化 key 自动失效；RatePolicy 写变更靠显式 invalidate（写路径只有一处）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cache_invalidation.py
"""Regression: cache invalidation after state/policy changes.

Covers two gaps left by the original TTL-only strategy:
1. RatePolicy change must reflect immediately (no 30s leak window).
2. Subject disable must invalidate policy_cache keyed by subject epoch.
"""
import pytest
from llm_gateway.services.cache import policy_cache, auth_cache


@pytest.fixture(autouse=True)
def _clear_caches():
    auth_cache._store.clear()
    policy_cache._store.clear()
    yield
    auth_cache._store.clear()
    policy_cache._store.clear()


# RatePolicy write path must call policy_cache.invalidate("rate:")
# See api/admin/policy.py — covered via integration test below.
```

加一条 integration 测试到 `tests/test_cache_invalidation.py`，用 `gateway_fixture` 建一个 subject + RatePolicy，resolve 一次填充缓存，然后改 RatePolicy.requests_per_minute 再 resolve，断言返回新值（而非缓存旧值）。具体 assertion 形态见 Step 4 实现。

- [ ] **Step 2: 验证测试失败**

Run: `uv run pytest tests/test_cache_invalidation.py -v`
Expected: FAIL — 改 RatePolicy 后 resolve 仍返回旧值

- [ ] **Step 3: policy_cache 版本化 key**

在 `rate_limit.py:resolve_effective_rate_policy` 里，cache key 构造前先取 subject.updated_at：

```python
async def resolve_effective_rate_policy(
    session: AsyncSession,
    *,
    key_id: UUID,
    subject_id: UUID,
    project_id: UUID,
    defaults: Settings,
) -> EffectiveRatePolicy:
    from llm_gateway.services.cache import policy_cache
    from llm_gateway.db.models import Subject

    subject = await session.get(Subject, subject_id)
    subject_epoch = int(subject.updated_at.timestamp()) if subject and subject.updated_at else 0
    cache_key = f"rate:{key_id}:{subject_id}:{project_id}:{subject_epoch}"
    cached = policy_cache.get(cache_key)
    if cached is not None:
        return cached
    # ... rest unchanged
```

- [ ] **Step 4: RatePolicy 写路径显式 invalidate**

在 `api/admin/policy.py` 的 RatePolicy 写处理函数（create/update/patch state）commit 后加：

```python
from llm_gateway.services.cache import policy_cache
policy_cache.invalidate("rate:")
```

（先 grep 确认 RatePolicy 的所有写路径，每个写路径都加。）

- [ ] **Step 5: 验证测试通过**

Run: `uv run pytest tests/test_cache_invalidation.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/llm_gateway/services/rate_limit.py src/llm_gateway/api/admin/policy.py tests/test_cache_invalidation.py
git commit -m "Version policy_cache key by subject epoch + invalidate on RatePolicy write

policy_cache had no invalidation: after an admin tightened rate limits,
the gateway kept the old (looser) policy for up to 30s. Two-layer fix:
(1) cache key now includes subject.updated_at epoch, so disabling or
modifying a subject naturally rotates the key;
(2) RatePolicy write paths explicitly call policy_cache.invalidate('rate:')
for direct policy edits (only a handful of write sites, won't balloon)."
```

### Task 1.2：marketplace admin 状态变更补审计（Critical C3）

**Files:**
- Modify: `src/llm_gateway/api/admin/marketplace.py:93-104,176-187`
- Test: `tests/test_marketplace_audit.py`

- [ ] **Step 1: 写失败测试**（PATCH `/admin/registry/skills/{id}/state` 后查 AuditEvent 表存在 action="skill.set_state" 记录）

- [ ] **Step 2: 验证失败**（当前无审计，测试 assert 失败）

- [ ] **Step 3: 修改两个 PATCH 函数**：在 `skill.state = payload.state` 之后、`commit` 之前插入 `_audit_update` 调用（见计划正文代码）

- [ ] **Step 4: 验证通过**

- [ ] **Step 5: Commit**

### Task 1.3：自服务 grant/like 写操作补审计（Important）

**Files:**
- Modify: `src/llm_gateway/api/auth.py` 的 8 个写函数
- Test: 扩展 `tests/test_marketplace_audit.py`

- [ ] **Step 1-5:** 同 1.2 模式，每个写操作 commit 前调 `record_audit_event` 显式传 `actor_subject_id=ctx.subject.id`

---

## Wave 2：死代码清理

### Task 2.1：删除 DuckDB 残留（Critical C5）
**Files:** Delete `scripts/fetch_duckdb_extensions.py`；Modify `README.md`、`config.py` 注释
- 验证：`grep -rn duckdb src scripts README.md` 零命中

### Task 2.2：删除根 main.py，统一入口（Important I16）
**Files:** Delete `main.py`；Modify `README.md`
- 决策：不做 editable install。start_local.py 功能更全。

### Task 2.3：清理 init_db.py 一次性逻辑（Minor）
**Files:** Modify `scripts/init_db.py`——删 LEGACY_SCHEMA_REVISION

### Task 2.4：删除前端 upstream-format.ts 化石（Minor）
**Files:** Delete `frontend/src/lib/upstream-format.ts`、`frontend/tests/upstream-format.test.ts`；Modify `+page.svelte`

### Task 2.5：删除 auth.py 影子 import + 空 panel（Minor）
**Files:** Modify `auth.py`（4 处内联 import）、`+page.svelte`（2 个空 panel）

---

## Wave 3：数据模型加固

### Task 3.1：Skill/MCP 复合唯一约束（Important I7）
**Files:** Create `alembic/versions/20260707_0014_unique_owner_slug.py`；Modify `db/models.py`
- 测试：并发同 slug 上传 → 第二个 IntegrityError
- 验证：upgrade/downgrade 双向绿

### Task 3.2：FK ondelete 策略（Important I6）⚠️ 谨慎
**Files:** Create `alembic/versions/20260707_0015_fk_on_delete.py`；Modify `db/models.py`
- 策略：事实表 SET NULL，从属表 CASCADE，用 NOT VALID 避免长锁
- **执行前确认目标 DB**

### Task 3.3：like_count 原子化（Important I8）
**Files:** Modify `services/registry.py:362-386,715-739`
- 测试：并发 toggle 不丢更新

---

## Wave 4：后端结构重构

### Task 4.1：抽 owner-name 解析 helper
- 5 处内联重复 → `resolve_owner_name_map(session, owner_ids)`

### Task 4.2：抽 artifact detail 构造 helper
- `build_skill_detail_payload` / `build_mcp_detail_payload`

### Task 4.3：upstream_client 四函数合一
- `_post_once(path)` / `_post_stream(path)`

### Task 4.4：registry.py 泛型化（共享 helper，非完整泛型基类）

### Task 4.5：拆分 auth.py 1672 行上帝文件 ⭐ 最大重构
- 拆为 auth_session/auth_keys/managed/marketplace_self + usage_queries service

### Task 4.6：api/__init__.py 路由聚合

---

## Wave 5：前端重构

### Task 5.1：抽 session store（.svelte.ts runes 模块）
### Task 5.2：抽 inventory store（含 SSE 消费）
### Task 5.3：admin section 抽组件（12 个 .svelte）
### Task 5.4：泛型化 MarketSection + GrantsEditor
### Task 5.5：统一 modal 组件（优先影响测试的 confirm/prompt）

---

## Wave 6：测试加固

### Task 6.1：test-only DB DSN + truncate 隔离
- config 加 `test_database_url`；conftest 用它 + 每测试 truncate

### Task 6.2：拆分 test_backend_integration.py（1501 行 → 7 个领域文件）

---

## 执行顺序与依赖
- Wave 0 独立先行（半天）
- Wave 1 依赖 Wave 0（1-2 天）
- Wave 2 独立于 1（半天，可并行）
- Wave 3 依赖 0、1（1 天）
- Wave 4 依赖 0、2（3-5 天，4.1→4.6 有序）
- Wave 5 独立于后端（3-5 天，可与 4 并行）
- Wave 6 依赖 0（2 天，6.1 优先于 6.2）

## 完成判据
- `uv run pytest -q` 全绿
- `cd frontend && npm run check && npm run test` 全绿
- `uv run pre-commit run --all-files` 绿
- `python -m compileall src scripts` 零错误
- 推送特性分支 `cleanup/debt-wave-0-5`

## 风险提示
- **Task 3.2（FK ondelete）**：schema 变更，执行前必须确认目标 DB，建议单独 PR
- **Task 4.5（拆 auth.py）**：最大重构，靠现有测试守护，放在 Wave 4 最后
- **Task 6.1（test DB）**：改变测试基础设施，可能暴露之前被真实库掩盖的 bug
