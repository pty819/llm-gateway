# 自助禁用/启用 Gateway Key

**日期**: 2026-06-29
**状态**: 已确认，待实现

## 背景与目标

当前用户登录后能通过 `POST /auth/keys` 给自己的个人 project 签发 gateway key，但签发后**既不能删也不能禁用**。key 一旦泄漏或不再使用，用户只能找管理员处理，体验差且增加 admin 负担。

本设计让用户能**自助禁用/启用自己创建的 key**。

## 设计原则

**自助 state 切换 = admin state 切换的效果，只是触发者从 admin 变成用户本人。** 两者都把 `GatewayKey.state` 在 `active`/`disabled` 间切换，鉴权层 `security.py` 已经过滤 `state == ACTIVE`，禁用的 key 立即失效。区别只记录在审计日志的 `action` 字段。

## 范围内

- `PATCH /auth/keys/{key_id}/state` 自助端点（禁用 + 启用，双向）
- 前端 `OwnedDashboard` 密钥表格加"操作"列，禁用/启用切换按钮
- 审计事件 `auth.key.set_state`

## 范围外

- **硬删除 key**：不做。会破坏 `request_facts.key_id` 外键，且禁用已能达到"key 失效"效果
- **二次确认弹窗**：不做。点击立即切换（用户可再次点击启用恢复）
- **管理非个人 project 的 key**：不做。用户只能动自己个人 project 下的 key

## 后端端点

新增 `PATCH /auth/keys/{key_id}/state`，紧挨现有 `POST /auth/keys`（`auth.py:600`）之后。复刻 admin 的 `set_gateway_key_state`（`identity.py:422`）模式，但加权限校验。

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
    key = await session.get(GatewayKey, key_id)
    personal_project = await _personal_project(session, context.subject)
    if key is None or key.subject_id != context.subject.id or key.project_id != personal_project.id:
        # 别人的 key 或跨 project 的 key，对当前用户而言"不存在"——404 而非 403，
        # 避免向用户泄露其他 key 的存在性（最小信息泄露）。
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

### 设计要点

1. **权限双重校验**：`subject_id == context.subject.id` **且** `project_id == 个人 project.id`。两条都满足才算"自己的 key"。任一不满足返回 404（不泄露存在性）。校验 `project_id` 是为了排除 admin 可能在非个人 project 下给该 subject 签的 key——那种 key 不属于"用户自己创建的"。

2. **state 双向**：禁用 + 启用都支持。`payload.state` 是 `ResourceState` StrEnum，pydantic 校验只能 `active`/`disabled`。误禁后用户可自己重新启用，符合"key 是用户自己的资产"。

3. **审计 action 区分**：`auth.key.set_state`（自助，`actor_subject_id=用户`）vs admin 的 `gateway_key.set_state`（管理员，`actor_subject_id=admin`）。

4. **立即生效**：`security.py` 鉴权时已过滤 `key.state != ACTIVE` 直接拒，无需额外失效逻辑。

5. **复用 `_personal_project`**：它幂等（已存在则返回），登录用户一定有个人 project（注册/首次发 key 时创建）。

## 前端展示

`OwnedDashboard.svelte` 的"网关密钥"表格（line 168-172）加一列"操作"：

```
| 名称 | 前缀 | 状态 | 操作 |
| my-key | gw-ab12… | [active] | [禁用] |   ← active → 显示"禁用"按钮
| old-key | gw-cd34… | [disabled] | [启用] |  ← disabled → 显示"启用"按钮
```

- 复用权限组管理（line 161）的"禁用/启用"切换按钮模式：`key.state === 'active' ? '禁用' : '启用'`
- 新增 prop `onSetOwnKeyState: (key, state: 'active'|'disabled') => void | Promise<void>`
- 父页面 `+page.svelte` 新增 `setOwnKeyState`：调 `PATCH /auth/keys/{id}/state`，成功后 `refreshProfile()` 刷新 keys 列表
- **不加二次确认弹窗**，点击立即切换

### 父页面 `+page.svelte` 改动

```typescript
async function setOwnKeyState(key, newState) {
    loading = true;
    try {
        await api.patch(`/auth/keys/${key.id}/state`, { state: newState });
        await refreshProfile();  // 重新拉 /auth/me，刷新 keys 列表
    } finally {
        loading = false;
    }
}
```

## 测试策略

### 后端测试（`tests/`，新增或追加）

| 场景 | 期望 |
|---|---|
| 用户禁用自己的 key（个人 project） | state→disabled，写 `auth.key.set_state` audit，actor=自己 |
| 用户启用自己已禁用的 key | state→active，写 audit |
| 用户尝试禁用**别人的 key** | 404 key_not_found，不写 audit |
| 用户尝试禁用**非个人 project 下**的 key（admin 给他在别的 project 签的） | 404 key_not_found |
| 未登录访问 | 401 |
| state 传非法值 | 422（pydantic 校验） |

### 前端测试

项目前端目前只有 `upstream-format.test.ts` 单测。本功能的交互改动较小（加一列 + 一个回调），不强制新增单测，靠手动验证。

## 改动文件清单

| 文件 | 改动 |
|---|---|
| `src/llm_gateway/api/auth.py` | 新增 `OwnKeyStatePatch` + `PATCH /auth/keys/{key_id}/state` 端点 |
| `frontend/src/lib/components/OwnedDashboard.svelte` | 密钥表格加"操作"列 + `onSetOwnKeyState` prop |
| `frontend/src/routes/+page.svelte` | 新增 `setOwnKeyState` 方法并传入 OwnedDashboard |
| 数据库迁移 | **无**（复用现有 `state` 字段） |
| 鉴权层 / security.py | **无改动**（已过滤 state） |
