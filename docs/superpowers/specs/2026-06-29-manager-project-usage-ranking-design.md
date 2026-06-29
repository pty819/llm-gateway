# Manager 查看项目内成员用量排名

**日期**: 2026-06-29
**状态**: 已确认，待实现

## 背景与目标

当前非 admin 用户作为 project manager 时，能通过 `GET /auth/managed/usage/summary?scope=project` 看到所管项目的**用量汇总数字**（总 token / 请求数），但**看不到每个成员各自的用量**。manager 想知道"项目里谁用得最多"必须找 admin 查。

admin 那边有 `/admin/usage/ranking`（走 DuckDB analytics 按人分组），但 manager 无权访问。

本设计让 manager 能自助查看**所管单个项目下每个成员的用量排名**，支持时间段 + 模型筛选，数据源走 Postgres（与现有 `managed/usage/summary` 同源）。

## 设计原则

**功能复用与一致性。** 查询范式复用现有 `_usage_summary_from_postgres`（同源 Postgres、同样的 token 聚合表达式、同样的 90 天上限）。权限复用现有 `_require_project_manager`。前端列定义对齐 admin ranking（用户/请求数/输入token/输出token/总token）。

## 范围内

- `GET /auth/managed/usage/ranking` 按 project 内 subject 分组的用量排名
- 必须选定单个项目（`project_id` 必填）
- 时间段筛选（沿用 90 天上限 + 默认最近 30 天）
- 模型筛选（可选）
- Top N 限制（默认 20，1-100）
- 前端在 `OwnedDashboard` 的"我管理的资源"面板加排名表格

## 范围外

- **跨项目合并排名**：不做。manager 必须选定单个项目
- **团队维度排名**：不做。只做 project 维度（团队是 subject 的松散分组，按 project 更贴合"项目预算"场景）
- **DuckDB analytics 通道**：不给 manager 开。走 Postgres，与现有 manager 用量同源
- **admin ranking 改动**：admin 那边完全不动

## 后端端点

新增 `GET /auth/managed/usage/ranking`，紧挨现有 `GET /auth/managed/usage/summary`（`auth.py:345`）之后。

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

    # 权限：必须是该 project 的 manager，否则 403
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

### 查询函数

新增 `_usage_ranking_from_postgres`，放在现有 `_usage_summary_from_postgres`（`auth.py:753`）旁边。复用同样的 `total_tokens` 聚合表达式和 Postgres 聚合范式。

```python
async def _usage_ranking_from_postgres(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    project_id: UUID,
    model: str | None = None,
    limit: int = 20,
) -> list[dict]:
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
            func.coalesce(func.sum(case(
                (col(RequestFact.outcome) == RequestOutcome.SUCCESS, 1), else_=0
            )), 0).label("success_count"),
            func.coalesce(func.sum(case(
                (col(RequestFact.outcome) != RequestOutcome.SUCCESS, 1), else_=0
            )), 0).label("failure_count"),
        )
        .select_from(RequestFact)
        .outerjoin(Subject, RequestFact.subject_id == Subject.id)
        .where(
            col(RequestFact.project_id) == project_id,
            col(RequestFact.started_at) >= start,
            col(RequestFact.started_at) < end,
            col(RequestFact.subject_id).isnot(None),  # 对齐 admin DuckDB ranking
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

### 设计要点

1. **`project_id` 必填**：作为查询参数但无默认值，pydantic 强制传。符合"必须选定单个项目"。

2. **权限用 Python 端校验**：`_require_project_manager` 在查询前拦截，非 manager 403。project_id 传进 SQL 时已是校验过的安全值，不依赖 SQL 层过滤正确性。

3. **90 天上限 + 默认时间窗**：和 `managed/usage/summary` 完全一致（`end - start > 90 天` 报错；都没传则默认最近 30 天）。

4. **`subject_id IS NOT NULL` 过滤**：对齐 admin DuckDB ranking 的 `rf.subject_id IS NOT NULL`，两边行为一致——匿名/系统请求不计入成员排名。

5. **排序**：`total_tokens DESC, request_count DESC`，和 admin DuckDB ranking 的 `ORDER BY` 完全一致。

6. **LEFT JOIN Subject**：即使 subject 行被删也能聚合（name 退化为 "无用户"），不因外键缺失丢数据。

7. **返回结构**：ranking 每行字段对齐 admin DuckDB ranking（`subject_id/subject_name/login_username/request_count/prompt_tokens/completion_tokens/total_tokens/success_count/failure_count`），前端可复用相近的展示逻辑。

## 前端展示

`OwnedDashboard.svelte` 的"我管理的资源"面板（line 124-163），在现有汇总 metric 下方加排名表格。

### 布局

```
[我管理的资源]
  范围: [项目▼]  资源: [某项目▼]  [查询管理范围用量]

  (现有汇总 metric：管理范围请求数 / 总 token)

  ── 项目成员用量排名 ──
  开始时间 [    ] 结束时间 [    ] 模型 [全部▼] Top N [20]  [查询排名]

  | # | 用户 | 请求数 | 输入token | 输出token | 总token |
  | 1 | 张三 | 152    | 1.2M     | 340K     | 1.5M    |
  | 2 | 李四 | 80     | 600K     | 120K     | 720K    |
```

### 新增 props（传给 OwnedDashboard）

- `managedRanking: ManagedRankingRow[]`（排名数据）
- `managedRankingStart: string` / `managedRankingEnd: string`（bindable 时间）
- `managedRankingModel: string`（bindable 模型筛选）
- `managedRankingLimit: number`（bindable Top N）
- `onRefreshManagedRanking: () => void | Promise<void>`（查询回调）

### 显示规则

- **排名区域只在 `managedUsageScope === 'project' && managedUsageResourceId` 时显示**。没选项目时隐藏（或提示"请选择项目"）。
- **独立"查询排名"按钮**：不自动随汇总查询触发，因为排名要额外的 model/limit 参数，独立触发更清晰。
- **排名表格用原生 `<table>`**（复用现有 OwnedDashboard 的表格样式），不引入新组件。
- **模型下拉**复用 `profile.models`。

### 父页面 `+page.svelte` 改动

参考现有 `refreshManagedUsage`（line 1051）：

```typescript
let managedRanking = $state<ManagedRankingRow[]>([]);
let managedRankingStart = $state('');
let managedRankingEnd = $state('');
let managedRankingModel = $state('');
let managedRankingLimit = $state(20);

async function refreshManagedRanking() {
    if (managedUsageScope !== 'project' || !managedUsageResourceId) return;
    loading = true;
    try {
        const data = await api.get('/auth/managed/usage/ranking', {
            project_id: managedUsageResourceId,
            start: managedRankingStart,
            end: managedRankingEnd,
            model: managedRankingModel,
            limit: managedRankingLimit
        });
        managedRanking = data.ranking;
    } finally {
        loading = false;
    }
}
```

### 类型定义

`frontend/src/lib/api/types.ts` 新增：

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

## 测试策略

### 后端测试

| 场景 | 期望 |
|---|---|
| manager 查自己管理的项目 | 返回按 total_tokens 降序的成员排名 |
| 非 manager 查该项目 | 403 not_project_manager |
| 普通用户（无管理资源）查任意项目 | 403 |
| 时间窗 > 90 天 | 400 time_window_exceeds_90_days |
| 都没传时间 → 默认最近 30 天 | 正常返回 |
| 传 model 筛选 | 只返回该 model 的用量 |
| limit 超出 1-100 | 422 |
| project_id 缺失 | 422 |
| 项目下某成员无请求 | 不出现在排名（聚合 count=0 不产生行） |

### 前端测试

交互改动较小，靠手动验证。重点验证：选项目后才显示排名区、查询按钮触发请求、表格按 token 降序展示。

## 改动文件清单

| 文件 | 改动 |
|---|---|
| `src/llm_gateway/api/auth.py` | 新增 `GET /auth/managed/usage/ranking` 端点 + `_usage_ranking_from_postgres` 查询函数 |
| `frontend/src/lib/components/OwnedDashboard.svelte` | "我管理的资源"面板加排名表格 + 相关 props |
| `frontend/src/routes/+page.svelte` | 新增排名状态变量 + `refreshManagedRanking` 方法并传入 OwnedDashboard |
| `frontend/src/lib/api/types.ts` | 新增 `ManagedRankingRow` 类型 |
| 数据库迁移 | **无**（纯查询，复用现有 `request_facts` + `subjects` 表） |
| admin / DuckDB analytics | **无改动** |
