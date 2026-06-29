# 自助 Key 管理 + Manager 用量排名 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户自助禁用/启用自己创建的 gateway key；让 project manager 能查看所管项目内每个成员的用量排名（时间段 + 模型筛选）。

**Architecture:** 两个独立功能，改动文件不重叠，顺序实现。功能1：新增 `PATCH /auth/keys/{id}/state` 端点（复刻 admin key state 端点 + 权限校验为个人 project）+ 前端密钥表格加操作列。功能2：新增 `GET /auth/managed/usage/ranking` 端点（Postgres 按人分组，复用 `_usage_summary_from_postgres` 范式）+ 前端管理面板加排名表格。两者都复用现有 `state` 字段 / 权限校验 / 审计机制，无 DB 迁移。

**Tech Stack:** Python 3、FastAPI、SQLAlchemy async、pytest（pytest-asyncio）、SvelteKit 5（runes `$state`/`$bindable`/`$derived`）、httpx ASGITransport 测试。

参考 spec：
- `docs/superpowers/specs/2026-06-29-self-key-disable-design.md`
- `docs/superpowers/specs/2026-06-29-manager-project-usage-ranking-design.md`

---

## File Structure

| 文件 | 责任 | 操作 | 功能 |
|---|---|---|---|
| `src/llm_gateway/api/auth.py` | 自助端点：key state 切换 + manager 用量排名 | 修改 | 1+2 |
| `frontend/src/lib/components/OwnedDashboard.svelte` | 非用户面板：密钥操作列 + 管理面板排名表格 | 修改 | 1+2 |
| `frontend/src/routes/+page.svelte` | 父页面状态 + 回调 | 修改 | 1+2 |
| `frontend/src/lib/api/types.ts` | `ManagedRankingRow` 类型 | 修改 | 2 |
| `tests/test_self_key_management.py` | key 禁用/启用端点测试 | 新增 | 1 |
| `tests/test_managed_usage_ranking.py` | manager 用量排名端点测试 | 新增 | 2 |

**不改动的文件：** `db/models.py`（无迁移）、`services/security.py`（已过滤 state）、`api/admin/*`（admin 完全不动）、DuckDB analytics。

---

# 功能1：自助禁用/启用 Key

## Task 1: 后端端点 — key state 切换（TDD）

**Files:**
- Modify: `src/llm_gateway/api/auth.py`（`issue_own_key` 函数之后，约 line 624）
- Test: `tests/test_self_key_management.py`

- [ ] **Step 1: 写失败测试 `tests/test_self_key_management.py`**

```python
from __future__ import annotations

from uuid import uuid4

import pytest


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _login_self_service_user(client):
    """Register a fresh self-service user and return (session_headers, subject_name)."""
    from tests.test_backend_integration import _employee_username

    username = _employee_username()
    register = await client.post(
        "/auth/register",
        json={"username": username, "full_name": "自助用户"},
    )
    assert register.status_code == 200, register.text
    login = await client.post(
        "/auth/login",
        json={"username": username, "password": "correct-horse-battery"},
    )
    assert login.status_code == 200, login.text
    headers = {"x-session-token": login.json()["session_token"]}
    return headers, username


async def _issue_own_key(client, headers, name="测试密钥"):
    response = await client.post("/auth/keys", json={"name": name}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["key"]


async def test_user_can_disable_own_key(client):
    headers, _ = await _login_self_service_user(client)
    key = await _issue_own_key(client, headers)

    response = await client.patch(
        f"/auth/keys/{key['id']}/state",
        json={"state": "disabled"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["key"]["state"] == "disabled"


async def test_user_can_re_enable_own_disabled_key(client):
    headers, _ = await _login_self_service_user(client)
    key = await _issue_own_key(client, headers)

    # 先禁用
    await client.patch(
        f"/auth/keys/{key['id']}/state",
        json={"state": "disabled"},
        headers=headers,
    )
    # 再启用
    response = await client.patch(
        f"/auth/keys/{key['id']}/state",
        json={"state": "active"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["key"]["state"] == "active"


async def test_disable_other_users_key_returns_404(client):
    victim_headers, _ = await _login_self_service_user(client)
    victim_key = await _issue_own_key(client, victim_headers)
    attacker_headers, _ = await _login_self_service_user(client)

    response = await client.patch(
        f"/auth/keys/{victim_key['id']}/state",
        json={"state": "disabled"},
        headers=attacker_headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "key_not_found"


async def test_disable_nonexistent_key_returns_404(client):
    headers, _ = await _login_self_service_user(client)

    response = await client.patch(
        f"/auth/keys/{uuid4()}/state",
        json={"state": "disabled"},
        headers=headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "key_not_found"


async def test_disable_key_without_session_returns_401(client):
    headers, _ = await _login_self_service_user(client)
    key = await _issue_own_key(client, headers)

    response = await client.patch(
        f"/auth/keys/{key['id']}/state",
        json={"state": "disabled"},
    )

    assert response.status_code == 401


async def test_disable_key_writes_audit_event(client):
    from sqlalchemy import select

    from llm_gateway.db.models import AuditEvent
    from llm_gateway.db.session import AsyncSessionLocal

    headers, _ = await _login_self_service_user(client)
    key = await _issue_own_key(client, headers)

    response = await client.patch(
        f"/auth/keys/{key['id']}/state",
        json={"state": "disabled"},
        headers=headers,
    )
    assert response.status_code == 200

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.resource_id == key["id"],
                    AuditEvent.action == "auth.key.set_state",
                )
                .order_by(AuditEvent.created_at.desc())
            )
        ).scalars().all()
        assert rows, "expected an auth.key.set_state audit row"
        latest = rows[0]
        assert latest.outcome == "success"
        assert latest.detail.get("state") == "disabled"
        assert latest.actor_subject_id is not None  # self-service, actor recorded
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -m pytest tests/test_self_key_management.py -v`
Expected: FAIL — 405 Method Not Allowed（端点不存在，FastAPI 对未定义的 PATCH 路径返回 405）或 404。

- [ ] **Step 3: 实现端点**

在 `src/llm_gateway/api/auth.py`：

(a) 找到 `issue_own_key` 函数（约 line 600-623），在它**之后**追加请求模型和端点：

```python
class OwnKeyStatePatch(BaseModel):
    state: ResourceState


@router.patch("/keys/{key_id}/state")
async def set_own_key_state(
    key_id: UUID,
    payload: OwnKeyStatePatch,
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    # 权限双重校验：key 必须属于当前用户的个人 project。
    # issue_own_key 只往个人 project 发 key，这两条等价于"自己创建的 key"。
    # 别人的 key 或跨 project 的 key，对当前用户而言"不存在"——404 而非 403，
    # 避免向用户泄露其他 key 的存在性（最小信息泄露）。
    key = await session.get(GatewayKey, key_id)
    personal_project = await _personal_project(session, context.subject)
    if (
        key is None
        or key.subject_id != context.subject.id
        or key.project_id != personal_project.id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="key_not_found")
    key.state = payload.state
    key.updated_at = utcnow()
    await record_audit_event(
        session,
        actor_subject_id=context.subject.id,
        action="auth.key.set_state",
        resource_type="gateway_key",
        resource_id=key.id,
        outcome="success",
        detail={"state": payload.state.value},
    )
    await session.commit()
    await session.refresh(key)
    return {"key": redact_gateway_key(key)}
```

(b) 确认 import 已就绪（这些符号在文件里应该都有，因为 `issue_own_key` 已用到 `GatewayKey`/`ResourceState`/`redact_gateway_key`/`record_audit_event`/`user_session_dep`/`utcnow`/`HTTPException`/`status`/`UUID`/`Depends`/`AsyncSession`/`session_dep`/`_personal_project`）。运行下面的检查命令确认没有 NameError。

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -c "import llm_gateway.api.auth"`
Expected: 无输出（import 成功，无 NameError）。

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -m pytest tests/test_self_key_management.py -v`
Expected: PASS（6 个测试全过）

- [ ] **Step 5: Commit**

```bash
cd /Users/liyifan/llm_gateway && git add src/llm_gateway/api/auth.py tests/test_self_key_management.py && git commit -m "Add PATCH /auth/keys/{id}/state for self-service key disable/enable"
```

---

## Task 2: 前端 — 密钥表格操作列

**Files:**
- Modify: `frontend/src/lib/components/OwnedDashboard.svelte`（密钥表格，约 line 168-172；props 定义约 line 8-98）
- Modify: `frontend/src/routes/+page.svelte`（新增 `setOwnKeyState` 函数 + 传入 prop）

- [ ] **Step 1: 在 OwnedDashboard props 加 `onSetOwnKeyState`**

在 `frontend/src/lib/components/OwnedDashboard.svelte`：

(a) 在 props 解构里（`onIssueOwnKey,` 之后，约 line 50）追加：

```typescript
	onSetOwnKeyState,
```

(b) 在 props 类型定义里（`onIssueOwnKey: () => void | Promise<void>;` 之后，约 line 95）追加：

```typescript
	onSetOwnKeyState: (key: { id: string; state: string }, state: 'active' | 'disabled') => void | Promise<void>;
```

- [ ] **Step 2: 在密钥表格加"操作"列**

在 `frontend/src/lib/components/OwnedDashboard.svelte`，找到密钥表格（`<h2>网关密钥</h2>` 下面的 `<table>`，约 line 171）。把整个表格替换为：

```svelte
		<div class="table-wrap"><table><thead><tr><th>名称</th><th>前缀</th><th>状态</th><th>操作</th></tr></thead><tbody>{#each profile?.keys ?? [] as key}<tr><td>{key.name}</td><td><code>{key.key_prefix}</code></td><td><StateBadge value={key.state} /></td><td><button class="secondary" type="button" onclick={() => onSetOwnKeyState(key, key.state === 'active' ? 'disabled' : 'active')} disabled={loading}>{key.state === 'active' ? '禁用' : '启用'}</button></td></tr>{:else}<tr><td colspan="4">还没有密钥。</td></tr>{/each}</tbody></table></div>
```

注意 `colspan` 从 3 改为 4（新增操作列）。按钮的"禁用/启用"切换 + disabled 绑定复用权限组管理（line 161）的范式。

- [ ] **Step 3: 在父页面新增 `setOwnKeyState` 函数**

在 `frontend/src/routes/+page.svelte`，找到 `issueOwnKey` 函数（约 line 705）之后，追加：

```typescript
	async function setOwnKeyState(key: { id: string; state: string }, newState: 'active' | 'disabled') {
		await run(async () => {
			await api.patch(`/auth/keys/${key.id}/state`, { state: newState });
			profile = await api.get<AuthProfile>('/auth/me');
		});
	}
```

`run` 是页面已有的包装器（处理 loading/pageError），`issueOwnKey` 用的就是它。`AuthProfile` 已在文件顶部 import。

- [ ] **Step 4: 把 `setOwnKeyState` 传入 OwnedDashboard**

在 `frontend/src/routes/+page.svelte`，找到 `OwnedDashboard` 组件调用处（搜索 `onIssueOwnKey={issueOwnKey}`，约 line 1282 附近）。在它旁边追加：

```svelte
					onSetOwnKeyState={setOwnKeyState}
```

- [ ] **Step 5: 前端类型检查 + 构建**

Run: `cd /Users/liyifan/llm_gateway/frontend && npm run check 2>&1 | tail -20`
Expected: 无类型错误（svelte-check 通过）。若命令不同，先 `cat package.json | grep -A5 '"scripts"'` 确认脚本名。

- [ ] **Step 6: Commit**

```bash
cd /Users/liyifan/llm_gateway && git add frontend/src/lib/components/OwnedDashboard.svelte frontend/src/routes/+page.svelte && git commit -m "Add disable/enable button to self-service keys table"
```

---

# 功能2：Manager 项目成员用量排名

## Task 3: 后端查询函数 — `_usage_ranking_from_postgres`（TDD）

**Files:**
- Modify: `src/llm_gateway/api/auth.py`（`_usage_summary_from_postgres` 之后，约 line 810）
- Test: `tests/test_managed_usage_ranking.py`

- [ ] **Step 1: 写失败测试 `tests/test_managed_usage_ranking.py`**

```python
from __future__ import annotations

from datetime import timedelta

import pytest

from llm_gateway.db.session import AsyncSessionLocal
from llm_gateway.services.facts import record_request_fact


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed_request_fact(*, project_id, subject_id, model_alias="test-model", total_tokens=100, outcome="success"):
    """Insert a minimal RequestFact row for aggregation tests."""
    from datetime import UTC, datetime

    from llm_gateway.db.models import EndpointFamily, RequestOutcome

    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        await record_request_fact(
            session,
            request_id=f"req-{project_id}-{subject_id}-{now.isoformat()}-{total_tokens}",
            started_at=now,
            ended_at=now,
            endpoint_family=EndpointFamily.CHAT_COMPLETIONS,
            subject_id=subject_id,
            subject_type="user",
            project_id=project_id,
            model_alias=model_alias,
            upstream_target_id=None,
            streaming=False,
            outcome=RequestOutcome.SUCCESS if outcome == "success" else RequestOutcome.ERROR,
            usage={"prompt_tokens": 10, "completion_tokens": total_tokens - 10, "total_tokens": total_tokens},
        )
        await session.commit()


async def test_usage_ranking_groups_by_subject_and_sorts_by_total_tokens():
    """直接调用查询函数：两个 subject 在同一 project，按 total_tokens 降序。"""
    from uuid import uuid4

    from llm_gateway.db.models import Project, Subject, SubjectType
    from llm_gateway.api.auth import _usage_ranking_from_postgres
    from datetime import UTC, datetime

    async with AsyncSessionLocal() as session:
        project = Project(name=f"rank-test-{uuid4().hex}", owner_subject_id=None)
        session.add(project)
        await session.flush()
        alice = Subject(name="Alice", type=SubjectType.USER)
        bob = Subject(name="Bob", type=SubjectType.USER)
        session.add_all([alice, bob])
        await session.flush()
        await session.commit()
        project_id = project.id
        alice_id = alice.id
        bob_id = bob.id

    await _seed_request_fact(project_id=project_id, subject_id=alice_id, total_tokens=500)
    await _seed_request_fact(project_id=project_id, subject_id=alice_id, total_tokens=300)
    await _seed_request_fact(project_id=project_id, subject_id=bob_id, total_tokens=100)

    now = datetime.now(UTC)
    start = now - timedelta(days=1)
    async with AsyncSessionLocal() as session:
        ranking = await _usage_ranking_from_postgres(
            session, start=start, end=now + timedelta(hours=1), project_id=project_id, limit=20
        )

    assert len(ranking) == 2
    assert ranking[0]["subject_name"] == "Alice"
    assert ranking[0]["total_tokens"] == 800
    assert ranking[0]["request_count"] == 2
    assert ranking[1]["subject_name"] == "Bob"
    assert ranking[1]["total_tokens"] == 100
    assert ranking[1]["request_count"] == 1


async def test_usage_ranking_filters_by_model():
    """传 model 参数时只聚合该 model 的用量。"""
    from uuid import uuid4

    from llm_gateway.db.models import Project, Subject, SubjectType
    from llm_gateway.api.auth import _usage_ranking_from_postgres
    from datetime import UTC, datetime

    async with AsyncSessionLocal() as session:
        project = Project(name=f"rank-model-{uuid4().hex}", owner_subject_id=None)
        session.add(project)
        await session.flush()
        alice = Subject(name="Alice2", type=SubjectType.USER)
        session.add(alice)
        await session.flush()
        await session.commit()
        project_id = project.id
        alice_id = alice.id

    await _seed_request_fact(project_id=project_id, subject_id=alice_id, model_alias="model-a", total_tokens=400)
    await _seed_request_fact(project_id=project_id, subject_id=alice_id, model_alias="model-b", total_tokens=600)

    now = datetime.now(UTC)
    start = now - timedelta(days=1)
    async with AsyncSessionLocal() as session:
        ranking = await _usage_ranking_from_postgres(
            session, start=start, end=now + timedelta(hours=1), project_id=project_id, model="model-a", limit=20
        )

    assert len(ranking) == 1
    assert ranking[0]["total_tokens"] == 400
    assert ranking[0]["subject_name"] == "Alice2"


async def test_usage_ranking_empty_project_returns_empty_list():
    from uuid import uuid4

    from llm_gateway.api.auth import _usage_ranking_from_postgres
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    start = now - timedelta(days=1)
    async with AsyncSessionLocal() as session:
        ranking = await _usage_ranking_from_postgres(
            session, start=start, end=now + timedelta(hours=1), project_id=uuid4(), limit=20
        )

    assert ranking == []
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -m pytest tests/test_managed_usage_ranking.py -v`
Expected: FAIL — `ImportError: cannot import name '_usage_ranking_from_postgres'`

- [ ] **Step 3: 实现查询函数**

在 `src/llm_gateway/api/auth.py`：

(a) 确认 import 区有这些符号（多数已有，因 `_usage_summary_from_postgres` 已用）。在文件顶部 import 区确保有：

```python
from sqlalchemy import case, desc, func, select, text
```

如果 `desc`/`text` 未导入则补上（`select`/`func`/`case` 应已存在）。

(b) 找到 `_empty_usage_summary` 函数（约 line 810），在它**之后**追加：

```python
async def _usage_ranking_from_postgres(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    project_id: UUID,
    model: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Per-subject usage ranking within a single project, sorted by total_tokens desc.

    Mirrors _usage_summary_from_postgres (same total_tokens coalesce expression,
    same Postgres aggregation) but groups by subject and orders by usage. Used by
    the manager-facing ranking endpoint; the manager permission check happens in
    the route handler before this runs.
    """
    total_tokens_expr = func.coalesce(
        RequestFact.total_tokens,
        func.coalesce(RequestFact.prompt_tokens, 0)
        + func.coalesce(RequestFact.completion_tokens, 0),
        0,
    )
    stmt = (
        select(
            Subject.id.label("subject_id"),
            Subject.name.label("subject_name"),
            Subject.login_username.label("login_username"),
            func.count(col(RequestFact.id)).label("request_count"),
            func.coalesce(func.sum(RequestFact.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(RequestFact.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(total_tokens_expr), 0).label("total_tokens"),
            func.coalesce(
                func.sum(
                    case((col(RequestFact.outcome) == RequestOutcome.SUCCESS, 1), else_=0)
                ),
                0,
            ).label("success_count"),
            func.coalesce(
                func.sum(
                    case((col(RequestFact.outcome) != RequestOutcome.SUCCESS, 1), else_=0)
                ),
                0,
            ).label("failure_count"),
        )
        .select_from(RequestFact)
        .outerjoin(Subject, RequestFact.subject_id == Subject.id)
        .where(
            col(RequestFact.project_id) == project_id,
            col(RequestFact.started_at) >= start,
            col(RequestFact.started_at) < end,
            col(RequestFact.subject_id).isnot(None),
        )
    )
    if model is not None:
        stmt = stmt.where(col(RequestFact.model_alias) == model)
    stmt = stmt.group_by(
        Subject.id, Subject.name, Subject.login_username
    ).order_by(
        desc(text("total_tokens")), desc(text("request_count"))
    ).limit(limit)
    rows = (await session.execute(stmt)).all()
    return [
        {
            "subject_id": str(row.subject_id),
            "subject_name": row.subject_name or "无用户",
            "login_username": row.login_username,
            "request_count": int(row.request_count),
            "prompt_tokens": int(row.prompt_tokens),
            "completion_tokens": int(row.completion_tokens),
            "total_tokens": int(row.total_tokens),
            "success_count": int(row.success_count),
            "failure_count": int(row.failure_count),
        }
        for row in rows
    ]
```

注意：`Subject` 需要在文件顶部已 import（`_profile_payload` 已用它，应该有）。确认 `RequestOutcome`/`RequestFact`/`col` 已 import（`_usage_summary_from_postgres` 已用）。

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -m pytest tests/test_managed_usage_ranking.py -v`
Expected: PASS（3 个测试全过）

- [ ] **Step 5: Commit**

```bash
cd /Users/liyifan/llm_gateway && git add src/llm_gateway/api/auth.py tests/test_managed_usage_ranking.py && git commit -m "Add _usage_ranking_from_postgres for per-subject project usage ranking"
```

---

## Task 4: 后端端点 — manager 用量排名（TDD）

**Files:**
- Modify: `src/llm_gateway/api/auth.py`（`managed_usage_summary` 之后，约 line 406）
- Test: `tests/test_managed_usage_ranking.py`（追加）

- [ ] **Step 1: 追加端点测试**

在 `tests/test_managed_usage_ranking.py` 末尾追加：

```python
async def _make_project_manager(client, admin_headers, project_name):
    """Create a self-service user, then add them as manager of a fresh project.

    project_memberships.role is a plain str ("manager"); _managed_projects_payload
    filters role == "manager" AND project.state == ACTIVE. Returns
    (manager_headers, project_id, username).
    """
    from tests.test_backend_integration import _employee_username
    from uuid import uuid4

    from llm_gateway.db.session import AsyncSessionLocal
    from llm_gateway.db.models import Project, Subject, ProjectMembership

    username = _employee_username()
    # 注册一个自助用户作为 manager
    await client.post("/auth/register", json={"username": username, "full_name": project_name})
    login = await client.post(
        "/auth/login", json={"username": username, "password": "correct-horse-battery"}
    )
    manager_headers = {"x-session-token": login.json()["session_token"]}

    # 建一个项目，把该用户加为 manager（role 是字符串 "manager"，非枚举）
    suffix = uuid4().hex
    async with AsyncSessionLocal() as session:
        subject = (
            await session.execute(
                select(Subject).where(col(Subject.login_username) == username)
            )
        ).scalar_one()
        project = Project(name=f"mgr-project-{suffix}", owner_subject_id=subject.id)
        session.add(project)
        await session.flush()
        membership = ProjectMembership(
            project_id=project.id,
            subject_id=subject.id,
            role="manager",
        )
        session.add(membership)
        await session.commit()
        project_id = project.id

    return manager_headers, project_id, username


async def test_manager_can_query_ranking_for_managed_project(client):
    from sqlalchemy import select

    from llm_gateway.db.models import Subject
    from llm_gateway.db.session import AsyncSessionLocal

    admin_headers, _ = await _admin_headers(client)
    manager_headers, project_id, manager_username = await _make_project_manager(
        client, admin_headers, "Manager1"
    )

    # 找到 manager 的 subject_id 用于造数据
    async with AsyncSessionLocal() as session:
        manager = (
            await session.execute(
                select(Subject).where(col(Subject.login_username) == manager_username)
            )
        ).scalar_one()
        manager_subject_id = manager.id

    await _seed_request_fact(project_id=project_id, subject_id=manager_subject_id, total_tokens=200)

    response = await client.get(
        "/auth/managed/usage/ranking",
        params={"project_id": str(project_id)},
        headers=manager_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == str(project_id)
    assert len(body["ranking"]) >= 1
    row = body["ranking"][0]
    assert row["total_tokens"] == 200
    assert set(row.keys()) == {
        "subject_id", "subject_name", "login_username",
        "request_count", "prompt_tokens", "completion_tokens",
        "total_tokens", "success_count", "failure_count",
    }


async def test_non_manager_cannot_query_project_ranking(client):
    admin_headers, _ = await _admin_headers(client)
    _, project_id, _ = await _make_project_manager(client, admin_headers, "Manager2")

    # 另一个普通用户（无管理权限）
    other_headers, _ = await _login_plain_user(client)

    response = await client.get(
        "/auth/managed/usage/ranking",
        params={"project_id": str(project_id)},
        headers=other_headers,
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "not_project_manager"


async def test_ranking_rejects_time_window_over_90_days(client):
    admin_headers, _ = await _admin_headers(client)
    manager_headers, project_id, _ = await _make_project_manager(client, admin_headers, "Manager3")

    from datetime import UTC, datetime

    start = datetime.now(UTC) - timedelta(days=100)
    end = datetime.now(UTC)
    response = await client.get(
        "/auth/managed/usage/ranking",
        params={"project_id": str(project_id), "start": start.isoformat(), "end": end.isoformat()},
        headers=manager_headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "time_window_exceeds_90_days"


async def test_ranking_without_session_returns_401(client):
    response = await client.get(
        "/auth/managed/usage/ranking",
        params={"project_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 401


async def test_ranking_missing_project_id_returns_422(client):
    headers, _ = await _login_plain_user(client)
    response = await client.get("/auth/managed/usage/ranking", headers=headers)
    assert response.status_code == 422


async def _admin_headers(client):
    from llm_gateway.core.config import get_settings

    login = await client.post(
        "/auth/login",
        json={
            "username": get_settings().bootstrap_admin_username,
            "password": get_settings().bootstrap_admin_password,
        },
    )
    return {"x-session-token": login.json()["session_token"]}, None


async def _login_plain_user(client):
    from tests.test_backend_integration import _employee_username

    username = _employee_username()
    await client.post("/auth/register", json={"username": username, "full_name": "普通用户"})
    login = await client.post(
        "/auth/login", json={"username": username, "password": "correct-horse-battery"}
    )
    return {"x-session-token": login.json()["session_token"]}, username
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -m pytest tests/test_managed_usage_ranking.py -k "test_manager_can_query or test_non_manager or test_ranking_rejects or test_ranking_without or test_ranking_missing" -v`
Expected: FAIL — 404（端点不存在）

- [ ] **Step 3: 实现端点**

在 `src/llm_gateway/api/auth.py`，找到 `managed_usage_summary` 函数（约 line 345-405），在它**之后**追加：

```python
@router.get("/managed/usage/ranking")
async def managed_usage_ranking(
    project_id: UUID,
    start: datetime | None = None,
    end: datetime | None = None,
    model: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    context: UserSessionContext = Depends(user_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    if start and end and (end - start).days > 90:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="time_window_exceeds_90_days",
        )
    if start is None and end is None:
        end = utcnow()
        start = end - timedelta(days=30)

    await _require_project_manager(session, context.subject.id, project_id)

    ranking = await _usage_ranking_from_postgres(
        session,
        start=start,
        end=end,
        project_id=project_id,
        model=model,
        limit=limit,
    )
    return {
        "start": start,
        "end": end,
        "project_id": project_id,
        "ranking": ranking,
    }
```

确认 import 区有 `Query`（FastAPI）。若没有，在 `from fastapi import ...` 行补上 `Query`。

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -m pytest tests/test_managed_usage_ranking.py -v`
Expected: PASS（全部测试通过，含 Task 3 的 3 个 + Task 4 的 6 个）

- [ ] **Step 5: Commit**

```bash
cd /Users/liyifan/llm_gateway && git add src/llm_gateway/api/auth.py tests/test_managed_usage_ranking.py && git commit -m "Add GET /auth/managed/usage/ranking endpoint with manager permission check"
```

---

## Task 5: 前端 — 类型定义 + 排名表格

**Files:**
- Modify: `frontend/src/lib/api/types.ts`
- Modify: `frontend/src/lib/components/OwnedDashboard.svelte`
- Modify: `frontend/src/routes/+page.svelte`

- [ ] **Step 1: 加 `ManagedRankingRow` 类型**

在 `frontend/src/lib/api/types.ts` 末尾追加：

```typescript
export interface ManagedRankingRow {
	subject_id: string;
	subject_name: string;
	login_username: string | null;
	request_count: number;
	prompt_tokens: number;
	completion_tokens: number;
	total_tokens: number;
	success_count: number;
	failure_count: number;
}
```

- [ ] **Step 2: 在 OwnedDashboard 加排名 props 和 UI**

在 `frontend/src/lib/components/OwnedDashboard.svelte`：

(a) 在 import 行（`import type { OwnUsageSummary, ... }`）的 type 列表里追加 `ManagedRankingRow`：

```typescript
	import type { ManagedRankingRow, OwnUsageSummary, ProjectMembership, Subject, TeamMembership } from '$lib/api/types';
```

(b) 在 props 解构里（`managedUsage,` 附近）追加：

```typescript
	managedRanking,
	managedRankingStart = $bindable(),
	managedRankingEnd = $bindable(),
	managedRankingModel = $bindable(),
	managedRankingLimit = $bindable(),
	onRefreshManagedRanking,
```

(c) 在 props 类型定义里（对应位置）追加：

```typescript
	managedRanking: ManagedRankingRow[];
	managedRankingStart: string;
	managedRankingEnd: string;
	managedRankingModel: string;
	managedRankingLimit: number;
	onRefreshManagedRanking: () => void | Promise<void>;
```

(d) 在"我管理的资源"面板里（`managedUsage` 的汇总 metric 之后、`</section>` 之前，约 line 138），插入排名区块。找到 `</section>` 结束"我管理的资源"面板的位置，在它前面插入：

```svelte
		{#if managedUsageScope === 'project' && managedUsageResourceId}
			<h3>项目成员用量排名</h3>
			<div class="form-grid">
				<label>开始时间<input type="datetime-local" bind:value={managedRankingStart} /></label>
				<label>结束时间<input type="datetime-local" bind:value={managedRankingEnd} /></label>
				<label>模型筛选<select bind:value={managedRankingModel}><option value="">全部</option>{#each profile?.models ?? [] as model}<option value={model}>{model}</option>{/each}</select></label>
				<label>Top N<input type="number" bind:value={managedRankingLimit} min="1" max="100" /></label>
				<button type="button" onclick={onRefreshManagedRanking} disabled={loading}>{loading ? '查询中' : '查询排名'}</button>
			</div>
			<div class="table-wrap"><table><thead><tr><th>#</th><th>用户</th><th>请求数</th><th>输入 token</th><th>输出 token</th><th>总 token</th></tr></thead><tbody>{#each managedRanking as row, i}<tr><td>{i + 1}</td><td>{row.subject_name}{row.login_username ? ` / ${row.login_username}` : ''}</td><td>{row.request_count}</td><td>{row.prompt_tokens}</td><td>{row.completion_tokens}</td><td>{row.total_tokens}</td></tr>{:else}<tr><td colspan="6" class="empty">暂无用量数据，请选择项目并查询。</td></tr>{/each}</tbody></table></div>
		{/if}
```

- [ ] **Step 3: 在父页面加排名状态和函数**

在 `frontend/src/routes/+page.svelte`：

(a) 在 `let managedUsage` 附近（约 line 133）追加状态变量：

```typescript
	let managedRanking = $state<ManagedRankingRow[]>([]);
	let managedRankingStart = $state('');
	let managedRankingEnd = $state('');
	let managedRankingModel = $state('');
	let managedRankingLimit = $state(20);
```

(b) 确认顶部 import 区有 `ManagedRankingRow`（在 `import type { ... } from '$lib/api/types'` 里追加它）。

(c) 在 `refreshManagedUsage` 函数（约 line 1051）之后追加：

```typescript
	async function refreshManagedRanking() {
		if (managedUsageScope !== 'project' || !managedUsageResourceId) return;
		await run(async () => {
			const params: Record<string, string> = {
				project_id: managedUsageResourceId,
				limit: String(managedRankingLimit)
			};
			if (managedRankingStart) params.start = managedRankingStart;
			if (managedRankingEnd) params.end = managedRankingEnd;
			if (managedRankingModel) params.model = managedRankingModel;
			const data = await api.get<{ ranking: ManagedRankingRow[] }>(
				'/auth/managed/usage/ranking',
				params
			);
			managedRanking = data.ranking;
		});
	}
```

(d) 在 `OwnedDashboard` 组件调用处（搜索 `onRefreshManagedUsage={refreshManagedUsage}`，约 line 1297），追加这些 props：

```svelte
					bind:managedRanking
					bind:managedRankingStart
					bind:managedRankingEnd
					bind:managedRankingModel
					bind:managedRankingLimit
					onRefreshManagedRanking={refreshManagedRanking}
```

- [ ] **Step 4: 前端类型检查 + 构建**

Run: `cd /Users/liyifan/llm_gateway/frontend && npm run check 2>&1 | tail -20`
Expected: 无类型错误。

- [ ] **Step 5: Commit**

```bash
cd /Users/liyifan/llm_gateway && git add frontend/src/lib/api/types.ts frontend/src/lib/components/OwnedDashboard.svelte frontend/src/routes/+page.svelte && git commit -m "Add manager project member usage ranking UI"
```

---

## Task 6: 全量回归

- [ ] **Step 1: 跑全量后端测试**

Run: `cd /Users/liyifan/llm_gateway && .venv/bin/python -m pytest tests/ 2>&1 | tail -5`
Expected: 全绿。重点关注新增的 `test_self_key_management.py` 和 `test_managed_usage_ranking.py`，以及原有测试无回归（尤其 `test_backend_integration.py`、`test_audit_*`）。

- [ ] **Step 2: 前端构建确认**

Run: `cd /Users/liyifan/llm_gateway/frontend && npm run build 2>&1 | tail -10`
Expected: 构建成功。

- [ ] **Step 3: 推送前自检**

Run: `cd /Users/liyifan/llm_gateway && git log --oneline -8`
确认提交链清晰，6 个功能提交（或合并后的对应数量）。

---

## Self-Review Checklist（实施前已自查）

**Spec 覆盖：**

功能1（自助 key）：
- ✅ PATCH state 端点（禁用+启用双向）→ Task 1
- ✅ 权限双重校验（个人 project + subject_id）→ Task 1 端点逻辑 + test_disable_other_users_key_returns_404
- ✅ 404 不泄露存在性 → test_disable_other_users_key_returns_404 / test_disable_nonexistent_key
- ✅ 审计 `auth.key.set_state` → test_disable_key_writes_audit_event
- ✅ 前端操作列 + 切换按钮（无二次确认）→ Task 2
- ✅ 父页面 setOwnKeyState + 刷新 profile → Task 2 Step 3

功能2（manager 排名）：
- ✅ GET ranking 端点（project_id 必填）→ Task 4
- ✅ manager 权限校验 → test_non_manager_cannot_query_project_ranking
- ✅ 90 天上限 + 默认 30 天 → test_ranking_rejects_time_window_over_90_days
- ✅ 模型筛选 → Task 3 test_usage_ranking_filters_by_model
- ✅ subject_id IS NOT NULL 过滤 → Task 3 查询函数 where 子句
- ✅ 按 total_tokens desc 排序 → Task 3 test_usage_ranking_groups_by_subject_and_sorts_by_total_tokens
- ✅ 字段对齐 admin ranking → Task 3 返回结构 + Task 5 类型定义
- ✅ 前端排名表格（时间+模型+TopN+表格）→ Task 5
- ✅ 只在选了 project 时显示 → Task 5 Step 2(d) `{#if managedUsageScope === 'project' && managedUsageResourceId}`

**无 DB 迁移**：两功能都复用现有字段/表，全程不动 models.py。
**admin 不受影响**：admin 端点/前端完全不改。
