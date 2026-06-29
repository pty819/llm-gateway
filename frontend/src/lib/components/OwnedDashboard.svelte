<script lang="ts">
	import type { ManagedRankingRow, OwnUsageSummary, ProjectMembership, Subject, TeamMembership } from '$lib/api/types';
	import StateBadge from '$lib/components/StateBadge.svelte';
	import CommandBlock from '$lib/components/CommandBlock.svelte';
	import CopyValue from '$lib/components/CopyValue.svelte';
	import { subjectDisplay } from '$lib/admin-config';

	let {
		profile,
		ownUsage,
		managedProjects,
		managedTeams,
		hasManagedResources,
		managedUsage,
		managedRanking,
		managedRankingStart = $bindable(),
		managedRankingEnd = $bindable(),
		managedRankingModel = $bindable(),
		managedRankingLimit = $bindable(),
		onRefreshManagedRanking,
		managedSubjectCandidates,
		managedRoles,
		managedProjectMemberships,
		managedTeamMemberships,
		membershipSubjectLabel,
		ownUsageStart = $bindable(),
		ownUsageEnd = $bindable(),
		managedUsageScope = $bindable(),
		managedUsageResourceId = $bindable(),
		managedProjectMemberForm = $bindable(),
		managedTeamMemberForm = $bindable(),
		managedSubjectSearch = $bindable(),
		ownKeyForm = $bindable(),
		gatewayBaseUrl = $bindable(),
		ownPasswordForm = $bindable(),
		preferredModel,
		visibleKeyHint,
		gatewayV1Base,
		responsesEndpoint,
		messagesEndpoint,
		gatewayOrigin,
		codexEnvCommand,
		codexConfigCommand,
		claudeEnvCommand,
		copiedItem,
		loading,
		onRefreshOwnUsage,
		onRefreshManagedUsage,
		onRefreshManagedSubjects,
		onRefreshManagedProjectMemberships,
		onRefreshManagedTeamMemberships,
		onAddManagedProjectMember,
		onRemoveManagedProjectMember,
		onAddManagedTeamMember,
		onSetManagedTeamMemberState,
		onIssueOwnKey,
		onSetOwnKeyState,
		onChangeOwnPassword,
		onCopy
	}: {
		profile: { subject: { login_username: string | null; name: string }; teams: string[]; models: string[]; keys: { id: string; name: string; key_prefix: string; state: string }[] } | null;
		ownUsage: OwnUsageSummary | null;
		managedProjects: { project: { id: string; name: string } }[];
		managedTeams: { team: { id: string; name: string } }[];
		hasManagedResources: boolean;
		managedUsage: OwnUsageSummary | null;
		managedRanking: ManagedRankingRow[];
		managedRankingStart: string;
		managedRankingEnd: string;
		managedRankingModel: string;
		managedRankingLimit: number;
		onRefreshManagedRanking: () => void | Promise<void>;
		managedSubjectCandidates: Subject[];
		managedRoles: { value: string; label: string }[];
		managedProjectMemberships: ProjectMembership[];
		managedTeamMemberships: TeamMembership[];
		membershipSubjectLabel: (m: ProjectMembership | TeamMembership) => string;
		ownUsageStart: string;
		ownUsageEnd: string;
		managedUsageScope: 'project' | 'team';
		managedUsageResourceId: string;
		managedProjectMemberForm: { resource_id: string; subject_id: string; role: string };
		managedTeamMemberForm: { resource_id: string; subject_id: string; role: string };
		managedSubjectSearch: string;
		ownKeyForm: { name: string };
		gatewayBaseUrl: string;
		ownPasswordForm: { current_password: string; new_password: string };
		preferredModel: string;
		visibleKeyHint: string;
		gatewayV1Base: string;
		responsesEndpoint: string;
		messagesEndpoint: string;
		gatewayOrigin: string;
		codexEnvCommand: string;
		codexConfigCommand: string;
		claudeEnvCommand: string;
		copiedItem: string;
		loading: boolean;
		onRefreshOwnUsage: () => void | Promise<void>;
		onRefreshManagedUsage: () => void | Promise<void>;
		onRefreshManagedSubjects: () => void | Promise<void>;
		onRefreshManagedProjectMemberships: () => void | Promise<void>;
		onRefreshManagedTeamMemberships: () => void | Promise<void>;
		onAddManagedProjectMember: () => void | Promise<void>;
		onRemoveManagedProjectMember: (m: ProjectMembership) => void | Promise<void>;
		onAddManagedTeamMember: () => void | Promise<void>;
		onSetManagedTeamMemberState: (m: TeamMembership, state: 'active' | 'disabled') => void | Promise<void>;
		onIssueOwnKey: () => void | Promise<void>;
		onSetOwnKeyState: (key: { id: string; state: string }, state: 'active' | 'disabled') => void | Promise<void>;
		onChangeOwnPassword: () => void | Promise<void>;
		onCopy: (value: string, key: string) => void | Promise<void>;
	} = $props();
</script>

<div class="page-header"><div><h1>我的访问权限</h1><p>{profile?.subject.login_username ?? profile?.subject.name}</p></div></div>
<div class="grid">
	<div class="metric"><span>权限组</span><strong>{profile?.teams.join(', ') || '无'}</strong></div>
	<div class="metric"><span>可用模型</span><strong>{profile?.models.length ?? 0}</strong></div>
	<div class="metric"><span>密钥</span><strong>{profile?.keys.length ?? 0}</strong></div>
	<div class="metric"><span>当前范围请求数</span><strong>{ownUsage?.request_count ?? 0}</strong></div>
	<div class="metric"><span>当前范围总 token</span><strong>{ownUsage?.total_tokens ?? 0}</strong></div>
</div>
<section class="panel">
	<h2>我的用量</h2>
	<div class="form-grid">
		<label>开始时间<input type="datetime-local" bind:value={ownUsageStart} /></label>
		<label>结束时间<input type="datetime-local" bind:value={ownUsageEnd} /></label>
		<button type="button" onclick={onRefreshOwnUsage} disabled={loading}>{loading ? '查询中' : '查询用量'}</button>
	</div>
	<div class="grid">
		<div class="metric"><span>请求数</span><strong>{ownUsage?.request_count ?? 0}</strong></div>
		<div class="metric"><span>输入 token</span><strong>{ownUsage?.prompt_tokens ?? 0}</strong></div>
		<div class="metric"><span>输出 token</span><strong>{ownUsage?.completion_tokens ?? 0}</strong></div>
		<div class="metric"><span>总 token</span><strong>{ownUsage?.total_tokens ?? 0}</strong></div>
		<div class="metric"><span>成功 / 失败</span><strong>{ownUsage?.success_count ?? 0} / {ownUsage?.failure_count ?? 0}</strong></div>
	</div>
</section>
{#if hasManagedResources}
	<section class="panel">
		<h2>我管理的资源</h2>
		<div class="grid">
			<div class="metric"><span>管理项目</span><strong>{managedProjects.length}</strong></div>
			<div class="metric"><span>管理权限组</span><strong>{managedTeams.length}</strong></div>
			<div class="metric"><span>管理范围请求数</span><strong>{managedUsage?.request_count ?? 0}</strong></div>
			<div class="metric"><span>管理范围总 token</span><strong>{managedUsage?.total_tokens ?? 0}</strong></div>
		</div>
		<div class="form-grid">
			<label>范围<select bind:value={managedUsageScope}><option value="project">项目</option><option value="team">权限组</option></select></label>
			<label>资源<select bind:value={managedUsageResourceId}><option value="">全部可管理资源</option>{#if managedUsageScope === 'project'}{#each managedProjects as item}<option value={item.project.id}>{item.project.name}</option>{/each}{:else}{#each managedTeams as item}<option value={item.team.id}>{item.team.name}</option>{/each}{/if}</select></label>
			<button type="button" onclick={onRefreshManagedUsage}>查询管理范围用量</button>
		</div>
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
	</section>
	<section class="panel">
		<h2>项目成员管理</h2>
		<div class="form-grid">
			<label>项目<select bind:value={managedProjectMemberForm.resource_id} onchange={onRefreshManagedProjectMemberships}><option value="">项目</option>{#each managedProjects as item}<option value={item.project.id}>{item.project.name}</option>{/each}</select></label>
			<label>搜索用户<input bind:value={managedSubjectSearch} placeholder="姓名或工号" /></label>
			<button class="secondary" type="button" onclick={onRefreshManagedSubjects}>搜索用户</button>
			<label>用户<select bind:value={managedProjectMemberForm.subject_id}><option value="">用户</option>{#each managedSubjectCandidates as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label>
			<label>角色<select bind:value={managedProjectMemberForm.role}>{#each managedRoles as role}<option value={role.value}>{role.label}</option>{/each}</select></label>
			<button type="button" onclick={onAddManagedProjectMember}>加入项目</button>
		</div>
		<div class="table-wrap"><table><thead><tr><th>用户</th><th>角色</th><th>操作</th></tr></thead><tbody>{#each managedProjectMemberships as membership}<tr><td>{membershipSubjectLabel(membership)}</td><td>{membership.role}</td><td><button class="secondary" type="button" onclick={() => onRemoveManagedProjectMember(membership)}>移除</button></td></tr>{:else}<tr><td colspan="3" class="empty">请选择项目并加载成员。</td></tr>{/each}</tbody></table></div>
	</section>
	<section class="panel">
		<h2>权限组成员管理</h2>
		<div class="form-grid">
			<label>权限组<select bind:value={managedTeamMemberForm.resource_id} onchange={onRefreshManagedTeamMemberships}><option value="">权限组</option>{#each managedTeams as item}<option value={item.team.id}>{item.team.name}</option>{/each}</select></label>
			<label>搜索用户<input bind:value={managedSubjectSearch} placeholder="姓名或工号" /></label>
			<button class="secondary" type="button" onclick={onRefreshManagedSubjects}>搜索用户</button>
			<label>用户<select bind:value={managedTeamMemberForm.subject_id}><option value="">用户</option>{#each managedSubjectCandidates as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label>
			<label>角色<select bind:value={managedTeamMemberForm.role}>{#each managedRoles as role}<option value={role.value}>{role.label}</option>{/each}</select></label>
			<button type="button" onclick={onAddManagedTeamMember}>加入权限组</button>
		</div>
		<div class="table-wrap"><table><thead><tr><th>用户</th><th>角色</th><th>状态</th><th>操作</th></tr></thead><tbody>{#each managedTeamMemberships as membership}<tr><td>{membershipSubjectLabel(membership)}</td><td>{membership.role}</td><td><StateBadge value={membership.state} /></td><td><button class="secondary" type="button" onclick={() => onSetManagedTeamMemberState(membership, membership.state === 'active' ? 'disabled' : 'active')}>{membership.state === 'active' ? '禁用' : '启用'}</button></td></tr>{:else}<tr><td colspan="4" class="empty">请选择权限组并加载成员。</td></tr>{/each}</tbody></table></div>
	</section>
{/if}
<section class="panel">
	<h2>可用模型</h2>
	<div class="table-wrap"><table><thead><tr><th>模型别名</th></tr></thead><tbody>{#each profile?.models ?? [] as model}<tr><td>{model}</td></tr>{:else}<tr><td>还没有可用模型。</td></tr>{/each}</tbody></table></div>
</section>
<section class="panel">
	<h2>网关密钥</h2>
	<div class="form-grid"><label>新密钥名称<input bind:value={ownKeyForm.name} /></label><button type="button" onclick={onIssueOwnKey}>签发密钥</button></div>
	<div class="table-wrap"><table><thead><tr><th>名称</th><th>前缀</th><th>状态</th><th>操作</th></tr></thead><tbody>{#each profile?.keys ?? [] as key}<tr><td>{key.name}</td><td><code>{key.key_prefix}</code></td><td><StateBadge value={key.state} /></td><td><button class="secondary" type="button" onclick={() => onSetOwnKeyState(key, key.state === 'active' ? 'disabled' : 'active')} disabled={loading}>{key.state === 'active' ? '禁用' : '启用'}</button></td></tr>{:else}<tr><td colspan="4">还没有密钥。</td></tr>{/each}</tbody></table></div>
</section>
<section class="panel">
	<h2>工具接入</h2>
	<div class="form-grid">
		<label>网关入口<input bind:value={gatewayBaseUrl} /></label>
		<label>首选模型<input value={preferredModel} readonly /></label>
		<label>当前密钥前缀<input value={visibleKeyHint} readonly /></label>
	</div>
	<div class="endpoint-grid">
		<CopyValue label="OpenAI Base URL" value={gatewayV1Base} itemKey="openai-base" {copiedItem} onCopy={onCopy} />
		<CopyValue label="Responses Endpoint" value={responsesEndpoint} itemKey="responses-endpoint" {copiedItem} onCopy={onCopy} />
		<CopyValue label="Claude Messages Endpoint" value={messagesEndpoint} itemKey="messages-endpoint" {copiedItem} onCopy={onCopy} />
		<CopyValue label="Claude Base URL" value={gatewayOrigin} itemKey="claude-base" {copiedItem} onCopy={onCopy} />
	</div>
	<div class="doc-grid">
		<section class="doc-panel">
			<h3>Codex</h3>
			<p>Codex 走 OpenAI Responses 协议，Base URL 使用前端入口的 <code>/v1</code>，实际请求会落到 <code>/v1/responses</code>。</p>
			<CommandBlock command={codexEnvCommand} />
			<CommandBlock command={codexConfigCommand} />
		</section>
		<section class="doc-panel">
			<h3>Claude Code</h3>
			<p>Claude Code 走 Anthropic Messages 协议，Base URL 填前端入口，不带 <code>/v1/messages</code>；客户端会自己拼接 <code>/v1/messages</code>。如果模型别名不是 <code>claude</code> 或 <code>anthropic</code> 开头，用自定义模型变量把它放进选择器。</p>
			<CommandBlock command={claudeEnvCommand} />
		</section>
	</div>
</section>
<section class="panel">
	<h2>修改密码</h2>
	<div class="form-grid">
		<label>当前密码<input type="password" bind:value={ownPasswordForm.current_password} autocomplete="current-password" /></label>
		<label>新密码<input type="password" bind:value={ownPasswordForm.new_password} autocomplete="new-password" /></label>
		<button type="button" onclick={onChangeOwnPassword}>修改密码</button>
	</div>
</section>
