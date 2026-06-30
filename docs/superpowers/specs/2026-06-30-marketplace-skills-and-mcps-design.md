# Marketplace: Skill & MCP Registry — Design

**Date:** 2026-06-30
**Status:** Approved (design); implementation scoped to Slice 1
**Author:** brainstormed with user

## Goal

为 LLM 网关添加一个 **Skill 市场** 和 **MCP 市场**，让下游 agent 能像逛 `skills.sh` 那样发现、拉取、安装 skill；让用户能自助上传 skill / MCP 配置，并决定哪些**权限组**（即现有 `Team`）可以访问。

## Confirmed Decisions

| 维度 | 决策 |
|---|---|
| 执行模型 | **纯注册表**——网关不执行任何东西，只做目录 + 鉴权门禁 + 存储分发 |
| Skill 形态 | 完整 **zip 包**（用户上传，下游拉取，agent 本地应用） |
| MCP 形态 | **连接配置**（transport + command/URL + args + env + headers + owner 手填的 tools 清单） |
| 上传者 | 任何已登录用户，**免审核**，上传即 active |
| 命名空间 | **owner/slug 二级命名**（`alice/weather`，`bob/weather` 可共存） |
| 版本管理 | **带版本历史**（保留历史版本，可拉指定版本或 latest） |
| 存储 | **Postgres BYTEA**（zip 内容） |
| 授权粒度 | **制品级**（授权给 team → 覆盖该制品的所有版本，含未来新版本） |
| 可见性 | **授权即一切**——授权给 `guest` team = 全市场可见（因为人人默认在 guest）；授权给特定 team = 仅该组可见；不授权 = 仅 owner 可见（private） |
| 拉取凭证 | 复用 **gateway key**（一个 key 既能调模型又能拉 skill/MCP） |
| MCP tools | **owner 手动填写**，网关不引入 MCP client 依赖 |
| 表结构 | **Skill 和 MCP 各自独立的两套表**（主表 + 版本表 + grant 表） |
| 版本模型 | Skill 和 MCP **统一用独立版本表**（主表存公共字段 + grant 锚点，版本表存每次变更），使授权逻辑统一写一次 |
| 路由组织 | **方案 A：数据面 + 自助管理面 + 超管面分离**，完全对齐现有 `/v1` `/auth` `/admin` 三面结构 |
| skill zip 大小 | 小文件（默认上限 10MB），Postgres BYTEA + 内存流式足够 |
| 产品分工 | 控制台聚焦"管理我的制品"；公开市场浏览主要靠 agent 通过 gateway key 调 `/v1/registry/*` |
| 交付范围 | **切片 1 优先**：地基 + Skill 市场完整闭环；MCP 为切片 2 |

## Architectural Fit

复用项目现有三大支柱，**不引入任何新概念**：

- **三种鉴权依赖全现成**：`auth_dep`（gateway key, `/v1`）、`user_session_dep`（session token, `/auth`）、`admin_dep`（admin token, `/admin`）。
- **授权模型完全复用 `ModelTeamGrant` 范式**：新增 `SkillTeamGrant` / `McpTeamGrant`，与现有 `model_team_grants` 结构逐字同构（`UNIQUE(artifact, team)`、`ix_*_state` 索引、每次请求实时重算不缓存）。
- **`Team` 即"权限组"**：前端 UI 已将 `teams` 标注为"权限组"（`frontend/src/lib/admin-config.ts`）。`guest` 是全员默认内置组，所以"授权给 guest" = "public"。
- **`Project.owner_subject_id` 是现有唯一的真 owner 列**；新表照搬此模式新增 `owner_subject_id`。

代码库**完全没有**任何 agent / skill / MCP / tool / plugin 概念——这是绿地新建。

---

## 1. Data Model

### 1.1 Enums

```python
class ArtifactKind(StrEnum):
    SKILL = "skill"
    MCP = "mcp"

class MCPTransport(StrEnum):
    STDIO = "stdio"   # 本地命令，agent 拉到后自行 spawn
    HTTP = "http"
    SSE = "sse"
```

> `ResourceState`（active/disabled）复用现有枚举，不新建。

### 1.2 Skill 制品（zip 内容包，主表 + 版本表）

```python
class Skill(TimestampMixin, table=True):
    __tablename__ = "skills"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    owner_subject_id: UUID = Field(foreign_key="subjects.id", index=True)
    slug: str = Field(index=True)
    # UNIQUE(owner_subject_id, slug) — name: uq_skill_owner_slug
    name: str
    summary: str | None = None
    description: str | None = None
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)
    latest_version: str | None = Field(default=None, index=True)
    notes: str | None = None


class SkillVersion(TimestampMixin, table=True):
    __tablename__ = "skill_versions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    skill_id: UUID = Field(foreign_key="skills.id", index=True)
    version: str = Field(index=True)
    # UNIQUE(skill_id, version) — name: uq_skill_version_skill_version
    content_blob: bytes                       # BYTEA, zip 内容
    content_sha256: str = Field(index=True)   # 完整性校验
    size_bytes: int
    upload_subject_id: UUID = Field(foreign_key="subjects.id")
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)
```

### 1.3 MCP 制品（连接配置，主表 + 版本表）

```python
class MCP(TimestampMixin, table=True):
    __tablename__ = "mcps"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    owner_subject_id: UUID = Field(foreign_key="subjects.id", index=True)
    slug: str = Field(index=True)
    # UNIQUE(owner_subject_id, slug) — name: uq_mcp_owner_slug
    name: str
    summary: str | None = None
    description: str | None = None
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)
    latest_version: str | None = Field(default=None, index=True)
    notes: str | None = None


class McpVersion(TimestampMixin, table=True):
    __tablename__ = "mcp_versions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    mcp_id: UUID = Field(foreign_key="mcps.id", index=True)
    version: str = Field(index=True)
    # UNIQUE(mcp_id, version) — name: uq_mcp_version_mcp_version
    transport: MCPTransport = Field(index=True)
    command: str | None = None
    args: list[str] = Field(default_factory=list, sa_column=Column(JSONB))
    env: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSONB))
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSONB))
    tools: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB))
        # owner 手填: [{name, description, input_schema}]
    upload_subject_id: UUID = Field(foreign_key="subjects.id")
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)
```

### 1.4 授权（复用 ModelTeamGrant 范式）

授权绑在**制品主表 id**（不是版本表），天然覆盖所有版本。

```python
class SkillTeamGrant(TimestampMixin, table=True):
    __tablename__ = "skill_team_grants"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    skill_id: UUID = Field(foreign_key="skills.id", index=True)
    team_id: UUID = Field(foreign_key="teams.id", index=True)
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)
    # UNIQUE(skill_id, team_id) — name: uq_skill_team_grant_skill_team


class McpTeamGrant(TimestampMixin, table=True):
    __tablename__ = "mcp_team_grants"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    mcp_id: UUID = Field(foreign_key="mcps.id", index=True)
    team_id: UUID = Field(foreign_key="teams.id", index=True)
    state: ResourceState = Field(default=ResourceState.ACTIVE, index=True)
    # UNIQUE(mcp_id, team_id) — name: uq_mcp_team_grant_mcp_team
```

### 1.5 latest_version 指针语义

- `latest_version` 是制品主表上的字符串指针，记录"当前推荐版本"的 version 号。
- 上传新版本时自动把 `latest_version` 指向新版本（新版本即成为 latest）。
- owner 可通过 `PATCH .../latest` 手动回滚指针到任意历史 active 版本。
- **下载 `versions/latest/download`** 等价于解析 `latest_version` → 取对应版本行。若 `latest_version` 为 NULL（无版本）→ 404 `version_not_found`。
- **禁用版本交互**：若 `latest_version` 指向的版本行被 disabled（软删），下载 latest 时回退取"该制品最新的 active 版本"（按 `created_at desc`），仍找不到则 404。这样禁用 latest 不会让制品突然不可下载，且 owner 应随后显式重设指针。

### 1.6 完整表清单（6 张新表）

| 表 | 用途 | 对齐的现有表 |
|---|---|---|
| `skills` | Skill 制品主表 | `model_aliases` |
| `skill_versions` | Skill 历史版本（含 zip blob） | 新（无现成对应） |
| `mcps` | MCP 制品主表 | `model_aliases` |
| `mcp_versions` | MCP 历史版本（配置） | 新 |
| `skill_team_grants` | Skill↔team 授权 | `model_team_grants` |
| `mcp_team_grants` | MCP↔team 授权 | `model_team_grants` |

---

## 2. API + Auth

完全对齐项目现有三面结构。

### 2.1 鉴权依赖（复用，不新增）

| 依赖 | 凭证 | 用在哪 |
|---|---|---|
| `auth_dep` | gateway key (`gw-`) | `/v1/registry/*` 数据面：agent 浏览、拉取 |
| `user_session_dep` | session token (`sess-`) | `/auth/registry/*` 自助面：上传、管理自己的制品、授权 |
| `admin_dep` | admin token / admin session | `/admin/registry/*` 超管面：兜底管理任何制品 |

### 2.2 数据面 `/v1/registry/*`（agent 消费，gateway key，只读）

```
GET  /v1/registry/skills                              列表（可见性过滤 + 搜索 + 分页）
GET  /v1/registry/skills/{owner}/{slug}               详情（含版本列表）
GET  /v1/registry/skills/{owner}/{slug}/versions/{ver}/download
GET  /v1/registry/skills/{owner}/{slug}/versions/latest/download

GET  /v1/registry/mcps                                列表
GET  /v1/registry/mcps/{owner}/{slug}                 详情（含版本 + 当前 latest 配置 + tools 清单）
GET  /v1/registry/mcps/{owner}/{slug}/versions/{ver}  取指定版本配置
```

**owner 路径参数解析**：先匹配 `subjects.login_username`，再匹配 `subjects.name`，取第一个 active 的。未找到 → 404。

**可见性过滤逻辑**（与 `subject_can_use_model` 同构，但只有两条路，无 direct-subject/project/key 路径）：

```python
# 一个 subject 能看到某制品 iff：
#   - 它是 owner，OR
#   - 存在 active 的 grant 把该制品授权给该 subject 所属的某个 active team
# （guest team 是全员默认组，"授权给 guest" = 全市场可见）
```

**列表查询参数**：`?q=`（搜 name/summary/slug，ilike）、`?owner=`（看某作者全部）、`?page=&size=` 分页（默认 30，上限 100）。

**MCP 敏感字段**：数据面 / 列表 / 详情中 `env`、`headers` 的 value 脱敏为 `***`（owner 在 `/auth` 面可看明文，admin 在 `/admin` 面可看明文）。

### 2.3 自助面 `/auth/registry/*`（session token，用户自助）

**Skill 上传**（multipart/form-data）：

```
POST /auth/registry/skills
  body 字段: slug, name, summary?, description?, version, notes?
  file 字段: skill.zip
  → 若 (owner, slug) 不存在 → 建 skill 主表 + 首个 version
  → 若已存在且 owner 是自己 → 追加新 version（同 version 号 → 409 version_conflict）
  → owner 不是自己但用同名 slug → **允许**（owner/slug 二级命名，alice/weather 与 bob/weather 共存，由复合 UNIQUE 约束保证完整性）
  → 服务端强校验大小 <= marketplace_skill_max_bytes
```

**管理自己的 Skill**：

```
GET   /auth/registry/skills                           我上传的列表
PATCH /auth/registry/skills/{owner}/{slug}            改 name/summary/description/state
DELETE /auth/registry/skills/{owner}/{slug}           禁用（state=disabled，软删）
GET   /auth/registry/skills/{owner}/{slug}/versions   版本历史
PATCH /auth/registry/skills/{owner}/{slug}/latest     设置 latest_version 指针
```

**Skill 授权管理**：

```
GET   /auth/registry/skills/{owner}/{slug}/grants              当前授权了哪些 team
POST  /auth/registry/skills/{owner}/{slug}/grants              { team_id } 授权给某 team（幂等 upsert）
PATCH /auth/registry/skills/{owner}/{slug}/grants/{grant_id}/state   撤销（=disabled）
```

**MCP 自助面完全对称**（`/auth/registry/mcps/*`），POST body 是 JSON（无文件）：

```
POST /auth/registry/mcps
  body: { slug, name, summary?, description?, version,
          transport, command?, args?, env?, url?, headers?, tools?, notes? }
```

**所有权校验**：所有 `/auth/registry/{kind}/{owner}/{slug}/*` 写操作先校验 `resolved_owner.id == session.subject.id`，否则 403 `not_artifact_owner`（对齐 `_require_project_manager` 的 ownership 检查模式）。

### 2.4 超管面 `/admin/registry/*`（admin token，兜底）

镜像 `model_team_grants` 的 admin CRUD 模式（`api/admin/access.py`），给 admin 跨越所有权的管理能力：

```
GET/PATCH/DELETE /admin/registry/skills/{owner}/{slug}          任意制品管理
GET/POST/PATCH   /admin/registry/skill-team-grants              grant CRUD（任意）
GET/POST/PATCH   /admin/registry/mcp-team-grants                grant CRUD
GET              /admin/registry/skills/{owner}/{slug}/versions 跨 owner 审计查看版本
```

每次写操作 `record_audit_event`（对齐 `access.py`）。

### 2.5 路由文件组织

```
src/llm_gateway/api/
├── proxy.py                 # 现有
├── auth.py                  # 现有 + 扩展：自助面注册表路由（或拆出 registry_auth.py）
├── registry.py              # 新：数据面 /v1/registry/*
├── admin/
│   ├── marketplace.py       # 新：超管面 /admin/registry/*
│   └── __init__.py          # 现有 + include 新子路由
```

`main.py` 的 `create_app()` 里 `app.include_router(registry.router)`。

### 2.6 错误码与响应

| 场景 | HTTP | detail |
|---|---|---|
| 未认证 / 凭证无效 | 401 | `missing_gateway_key` / `invalid_gateway_key` / `missing_session_token` / `invalid_session_token` |
| 非 owner 改别人制品 | 403 | `not_artifact_owner` |
| 制品不存在或无权访问 | 404 | `artifact_not_found` |
| 同 (owner, slug) 重复 POST 且非追加版本 | 409 | `artifact_slug_conflict` |
| 同制品重复版本号 | 409 | `version_conflict` |

**安全细节**：制品不存在与无权访问**都返回 404**（不泄露存在性），对齐现有 `model_not_entitled` 的处理思路。

---

## 3. Service Layer + Policy

### 3.1 新增服务模块

```
src/llm_gateway/services/registry.py   # 新：市场领域逻辑（纯函数 + async，不持有状态）
```

**为什么单文件**：skill 和 MCP 共享 90% 逻辑（owner 解析、可见性过滤、grant upsert、版本管理），用泛型函数 + `ArtifactKind` 区分。比拆成 `skill_service.py` + `mcp_service.py` 少一半重复。文件预计 ~300 行，仍在可 hold 范围内。

### 3.2 核心函数（对齐 policy.py 风格）

**Owner 解析**：

```python
async def resolve_owner_subject(session: AsyncSession, *, owner: str) -> Subject | None:
    """URL 路径里的 owner 名 → Subject。先匹配 login_username，再匹配 name，
    取第一个 active。未找到返回 None（路由层转 404）。"""
```

**可见性判定**（与 `subject_can_use_model` 同构，但只两条路）：

```python
async def subject_can_access_artifact(
    session: AsyncSession, *, kind: ArtifactKind, subject: Subject, artifact_id: UUID,
) -> bool:
    # 1) owner 即可见
    # 2) team grant（与 subject_can_use_model 的 team 分支完全同构）
```

**可见列表查询**（与 `list_accessible_model_aliases` 同构）：UNION 两路——owner 是自己的 + team grant 授权的。用 `selectinload` 预取 owner 的 username/name 供列表卡片显示。

**上传 / 追加版本**：

```python
async def create_or_append_skill_version(
    session: AsyncSession, *,
    actor: Subject, slug: str, version: str,
    name: str, summary: str | None, description: str | None, notes: str | None,
    zip_bytes: bytes,
) -> Skill:
    sha = hashlib.sha256(zip_bytes).hexdigest()
    # 1) 查 (actor.id, slug) 是否已存在
    #    不存在 → 建 skill 主表 + 首个 version；latest_version = version
    #    已存在且是 owner → 更新主表元数据 + 追加 version（新版本自动成为 latest）
    #    已存在但 owner 非自己 → 409 artifact_slug_conflict
    # 2) 写 SkillVersion（content_blob/size_bytes/content_sha256/upload_subject_id）
    # 3) record_audit_event(action="skill.upload", ...)
```

MCP 的 `create_or_append_mcp_version` 对称（version 行存配置字段，无 blob）。

**Grant upsert**（对齐 `ensure_model_team_grant` 的幂等范式，`services/security.py:291-312`）：

```python
async def ensure_skill_team_grant(session, *, skill_id, team_id) -> SkillTeamGrant:
    # 已存在则把 state 重置为 active，否则新建。避免重复 grant。
async def ensure_mcp_team_grant(session, *, mcp_id, team_id) -> McpTeamGrant:
```

### 3.3 敏感字段脱敏（对齐 `UpstreamTarget.api_key_value` 范式）

```python
# services/resource_payloads.py 新增
SENSITIVE_MCP_FIELDS = {"env", "headers"}

def redact_mcp_version(mcp_version: McpVersion, *, reveal: bool = False) -> dict:
    """reveal=False（数据面/列表/详情）→ env/headers 的 value 替换为 '***'
       reveal=True（owner 在 /auth 面查自己）→ 明文"""
```

把这些字段加入 `services/facts.py:_AUDIT_SENSITIVE_KEYS`，确保 audit 不落明文密钥。

### 3.4 下载（zip 流式返回）

```python
@router.get("/skills/{owner}/{slug}/versions/{ver}/download")
async def download_skill_version(owner, slug, ver, auth=Depends(auth_dep)):
    artifact = await _resolve_visible_skill_or_404(session, owner, slug, auth.subject)
    version = await _get_version_or_404(session, artifact.id, ver)
    return StreamingResponse(
        io.BytesIO(version.content_blob),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{slug}-{ver}.zip"',
            "X-Content-SHA256": version.content_sha256,
            "ETag": version.content_sha256,
        },
    )
```

### 3.5 权限决策矩阵

| 操作 | 数据面 `/v1` (gateway key) | 自助面 `/auth` (session) | 超管面 `/admin` |
|---|---|---|---|
| 浏览可见列表 | ✅ | ✅（仅自己上传的） | ✅（全部） |
| 看详情/配置（MCP 敏感脱敏） | ✅ | ✅ owner 明文 | ✅ 明文 |
| 下载 zip / 取配置 | ✅ 可见即可 | ✅ owner | ✅ |
| 上传新制品/版本 | ❌ | ✅ | ✅ |
| 改 metadata / state | ❌ | ✅ 仅自己的 | ✅ |
| 授权给 team | ❌ | ✅ 仅自己的 | ✅ |
| 跨 owner 管理 | ❌ | ❌ | ✅ |

---

## 4. Frontend + Upload Flow

### 4.1 导航结构

在 `frontend/src/lib/admin-config.ts` 的 `sections` 数组新增两个条目，归入新导航组 **"市场"**：

```typescript
{ id: 'skill-market', label: 'Skill 市场', group: '市场', icon: Package },
{ id: 'mcp-market',   label: 'MCP 市场',  group: '市场', icon: Plug },
```

`navGroups` 自动包含"市场"。

### 4.2 API client 扩展

`frontend/src/lib/api/types.ts` 新增类型：

```typescript
export interface SkillSummary {
  id: string; owner_subject_id: string; owner_name: string;
  slug: string; name: string; summary: string | null;
  state: string; latest_version: string | null; updated_at: string;
}
export interface SkillDetail extends SkillSummary {
  description: string | null; notes: string | null;
  versions: SkillVersionSummary[];
  grants: SkillTeamGrantSummary[];
}
export interface SkillVersionSummary {
  version: string; content_sha256: string; size_bytes: number;
  upload_subject_id: string; created_at: string; state: string;
}
// MCP 类型对称：McpSummary / McpDetail / McpVersionDetail（env/headers 已脱敏 ***）
```

### 4.3 产品定位分工

- **控制台侧**：聚焦"管理我的制品"——上传、版本管理、授权编辑。浏览列表只显示自己上传的。
- **agent 侧**：公开市场浏览 + 拉取，通过 gateway key 调 `/v1/registry/*`。

### 4.4 关键 UI 区块（复用现有组件）

复用：`ResourceTable`（列表）、`StateBadge`、`Pagination`、`SecretOnceDialog`（如有一次性展示需求）。

**Skill 市场页（`skill-market`）**：

```
┌─ 我的制品（我上传的 skill）──────────────────┐
│  [+ 上传新 Skill]                            │
│  ResourceTable: slug | name | latest_version │
│     | state | 版本数 | 授权team数 | 操作     │
└──────────────────────────────────────────────┘
点行 → SkillDetailDrawer（抽屉）
  ├─ 基本信息（name/summary/description/state）
  ├─ 版本历史（version, sha256 前8位, 大小, 上传时间, [下载][设为latest]）
  ├─ 权限组授权（已授权 team 列表 + [添加授权] 选 team 下拉）
  └─ [上传新版本] [禁用制品]
```

**MCP 市场页（`mcp-market`）**——对称结构，配置信息含 transport/command或url/args/env\*\*\*/headers\*\*\*/tools 清单。

### 4.5 上传流程

**Skill 上传（multipart）**：

```
[+ 上传新 Skill] → UploadSkillDialog
  ├─ 元数据表单：slug*、name*、version*、summary、description、notes
  ├─ 文件选择：拖拽/点击选 .zip（前端校验 MIME + 大小 < 10MB）
  ├─ 若 slug 已存在于"我的制品"：提示"将追加为新版本"，version 必须不同于现有
  └─ [确认上传] → POST /auth/registry/skills (multipart)
```

**slug 规范**：前端校验 `^[a-z][a-z0-9-]*$`（小写+连字符）。

**MCP 新建（JSON 表单，无文件）**：

```
[+ 新建 MCP 配置] → CreateMcpDialog
  ├─ 基本：slug*、name*、version*、summary、description
  ├─ transport 选择（stdio/http/sse）→ 动态表单
  │    stdio:  command*、args[]（动态行）、env{key:val}（动态行）
  │    http/sse: url*、headers{key:val}（动态行）
  ├─ tools 清单编辑器：[+ 添加 tool] → 每行 name*/description/input_schema(JSON textarea)
  └─ [确认创建] → POST /auth/registry/mcps (JSON)
```

### 4.6 Agent 侧消费体验（核心诉求）

```bash
GW_KEY="gw-xxx"
# 1) 浏览市场
curl /v1/registry/skills?q=weather -H "Authorization: Bearer $GW_KEY"
# 2) 看详情
curl /v1/registry/skills/alice/weather -H "Authorization: Bearer $GW_KEY"
# 3) 拉 latest zip（agent 本地解压加载）
curl /v1/registry/skills/alice/weather/versions/latest/download \
  -H "Authorization: Bearer $GW_KEY" -o ~/.skills/alice-weather.zip
# 4) MCP 配置拉取（agent 本地据此连接 MCP server）
curl /v1/registry/mcps/bob/weather-mcp -H "Authorization: Bearer $GW_KEY"
```

**可选增强（非首版）**：`scripts/marketplace_install.py` 封装搜索→选择→下载→解压流程。

### 4.7 前端文件变更清单

```
frontend/src/
├── lib/admin-config.ts                    # 改：+2 section
├── lib/api/types.ts                       # 改：+ Skill*/Mcp* 类型
├── lib/api/client.ts                      # 改：+ marketplace 方法
└── lib/components/
    ├── SkillMarketSection.svelte          # 新
    ├── McpMarketSection.svelte            # 新（切片 2）
    ├── UploadSkillDialog.svelte           # 新
    ├── CreateMcpDialog.svelte             # 新（切片 2）
    └── ArtifactGrantsEditor.svelte        # 新（skill/mcp 共用）
```

`+page.svelte` 加两个 view 分支渲染对应 Section 组件。

---

## 5. Migration, Config, Scope, Testing

### 5.1 数据库迁移

**单文件**：`alembic/versions/20260630_0011_marketplace_skills_and_mcps.py`，对齐 `20260525_0002_auth_teams.py` 形状。

- `revision = "20260630_0011"`，`down_revision = "20260629_0010"`
- `resourcestate` ENUM 用 `create_type=False`（复用现有）
- 建表顺序：skills → skill_versions → mcps → mcp_versions → skill_team_grants → mcp_team_grants
- 每张表：PK `id`、`created_at`/`updated_at`（from `TimestampMixin`）、各 FK、唯一约束（`uq_*`）、`ix_*_state` 等索引
- `downgrade`：反向 drop 所有 index/table（逆序，对齐 `0002`）

### 5.2 配置项（`core/config.py`）

```python
marketplace_skill_max_bytes: int = Field(
    default=10 * 1024 * 1024, alias="LLM_GATEWAY_MARKETPLACE_SKILL_MAX_BYTES"
)
marketplace_list_default_size: int = Field(
    default=30, alias="LLM_GATEWAY_MARKETPLACE_LIST_DEFAULT_SIZE"
)
marketplace_list_max_size: int = Field(
    default=100, alias="LLM_GATEWAY_MARKETPLACE_LIST_MAX_SIZE"
)
```

`.env.example` 同步加这 3 条文档。

### 5.3 范围切割

```
切片 1（首版，本次 plan 范围）: 地基 + Skill 市场（核心闭环）
  ├─ 迁移（全部 6 张表）
  ├─ 6 个 SQLModel 实体
  ├─ services/registry.py：owner 解析 + 可见性判定 + skill 版本管理 + grant upsert + 脱敏
  ├─ /v1/registry/skills/* 数据面（列表/详情/下载）
  ├─ /auth/registry/skills/* 自助面（上传/管理/授权）
  ├─ /admin/registry/skills/* + skill-team-grants 超管面
  └─ 前端 Skill 市场页 + 上传对话框 + 授权编辑器

切片 2: MCP 市场（复用地基，增量小）
  ├─ services/registry.py：+ MCP 版本管理（逻辑同构 skill）
  ├─ /v1/registry/mcps/* + /auth/registry/mcps/* + /admin/registry/mcps/*
  ├─ 敏感字段脱敏实现（env/headers → ***）
  └─ 前端 MCP 市场页 + 新建对话框（复用 ArtifactGrantsEditor）

切片 3: 体验打磨（可选，非阻塞）
  ├─ Agent 侧 install 脚本
  ├─ 下载 ETag/缓存
  ├─ 市场搜索增强（全文/标签）
  └─ admin 全市场浏览视图
```

### 5.4 测试策略（对齐现有集成测试风格）

真实 Postgres + Redis 集成测试（`tests/conftest.py` 跑真实 Alembic 迁移 + httpx ASGI transport）。复用 `gateway_fixture`。

```
tests/
├── test_marketplace_skills.py     # 切片 1
│   ├─ test_upload_skill_creates_artifact_and_version
│   ├─ test_upload_duplicate_slug_same_owner_appends_version
│   ├─ test_upload_duplicate_slug_different_owner_allowed
│   ├─ test_upload_version_conflict_409
│   ├─ test_download_latest_and_specific_version
│   ├─ test_download_integrity_sha256_matches
│   ├─ test_list_visibility_guest_grant_equals_public
│   ├─ test_list_visibility_unauthorized_team_hidden
│   ├─ test_owner_can_manage_non_owner_forbidden_403
│   ├─ test_grant_lifecycle_authorize_then_revoke
│   └─ test_admin_can_manage_any_artifact
├── test_marketplace_mcps.py       # 切片 2
│   ├─ test_create_mcp_config
│   ├─ test_mcp_sensitive_fields_redacted_in_data_plane
│   ├─ test_mcp_sensitive_fields_revealed_to_owner
│   └─ test_mcp_tools_list_returned
└── test_marketplace_security.py   # 横切
    ├─ test_no_gateway_key_401
    ├─ test_gateway_key_sees_only_authorized
    ├─ test_download_nonexistent_returns_404_not_403
    └─ test_audit_events_recorded_for_writes
```

### 5.5 安全审查清单

| 项 | 措施 |
|---|---|
| 存在性不泄露 | 无权访问 → 404（与有权但不存在同响应） |
| 跨 owner 写保护 | 所有 `/auth` 写操作校验 `owner == session.subject` |
| MCP 密钥脱敏 | 数据面/列表/详情 `env`/`headers` 值 → `***`；owner 在 `/auth` 明文 |
| Audit 落库 | 所有写操作 `record_audit_event`；敏感 key 加入 `_AUDIT_SENSITIVE_KEYS` |
| 上传大小限制 | 服务端强校验 `len(zip_bytes) <= marketplace_skill_max_bytes`（不只信前端） |
| zip 内容安全 | 记录 sha256；**首版不做 zip 内容扫描**（路径穿越/恶意内容扫描列为后续） |
| 下载鉴权 | 每次 download 走 `auth_dep` + 可见性重算（不缓存授权，对齐 policy.py 哲学） |

### 5.6 不做的事（YAGNI 明确排除）

- ❌ 网关托管运行时（执行在 agent 本地）
- ❌ 审核流程（免审核，立即可见）
- ❌ MCP client 依赖（tools 清单 owner 手填）
- ❌ 对象存储（BYTEA 够用）
- ❌ 版本级授权（制品级即可）
- ❌ 可见性字段（授权即一切，guest=public）
- ❌ 计费（不额外记录拉取，或后续切片再加）

---

## Implementation Plan Scope

**本次写 implementation plan 只覆盖切片 1**（地基 + Skill 市场完整闭环）。切片 2（MCP）待切片 1 验证架构后再独立规划。

切片 1 虽然只做 Skill，但会**一次性建立全部 6 张表和全部 6 个 SQLModel 实体**（MCP 的表和实体也建好），这样切片 2 无需再动数据库，只需补 MCP 的服务函数、路由、前端。
