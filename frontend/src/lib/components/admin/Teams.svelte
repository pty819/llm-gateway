<script lang="ts">
	import type { Inventory, ResourceState } from '$lib/api/types';
	import { PAGE_SIZE, short, subjectDisplay } from '$lib/admin-config';
	import StateBadge from '$lib/components/StateBadge.svelte';
	import PageTitle from '$lib/components/PageTitle.svelte';
	import Pagination from '$lib/components/Pagination.svelte';

	type TeamForm = { name: string; notes: string };
	type TeamMembershipForm = { team_id: string; subject_id: string; role: string };
	type ModelTeamGrantForm = { model_alias_id: string; team_id: string };

	let {
		teams,
		models,
		modelTeamGrants,
		managedRoles,
		teamMembershipRows,
		teamMembershipPageRows,
		teamForm = $bindable(),
		teamMembershipForm = $bindable(),
		modelTeamGrantForm = $bindable(),
		teamSubjectSearch = $bindable(),
		teamMembershipTeamFilter = $bindable(),
		teamMembershipSubjectSearch = $bindable(),
		teamMembershipRoleFilter = $bindable(),
		teamMembershipStateFilter = $bindable(),
		teamMembershipPage = $bindable(),
		subjectOptions,
		subjectLabel,
		modelLabel,
		teamLabel,
		onCreateTeam,
		onCreateTeamMembership,
		onCreateModelTeamGrant,
		onPatchTeam,
		onSetTeamMembershipState,
		onSetModelTeamGrantState
	}: {
		teams: Inventory['teams'];
		models: Inventory['models'];
		modelTeamGrants: Inventory['modelTeamGrants'];
		managedRoles: { value: string; label: string }[];
		teamMembershipRows: Inventory['teamMemberships'];
		teamMembershipPageRows: Inventory['teamMemberships'];
		teamForm: TeamForm;
		teamMembershipForm: TeamMembershipForm;
		modelTeamGrantForm: ModelTeamGrantForm;
		teamSubjectSearch: string;
		teamMembershipTeamFilter: string;
		teamMembershipSubjectSearch: string;
		teamMembershipRoleFilter: string;
		teamMembershipStateFilter: string;
		teamMembershipPage: number;
		subjectOptions: (query: string) => Inventory['subjects'];
		subjectLabel: (id: string | null | undefined) => string;
		modelLabel: (id: string | null | undefined) => string;
		teamLabel: (id: string | null | undefined) => string;
		onCreateTeam: () => void;
		onCreateTeamMembership: () => void;
		onCreateModelTeamGrant: () => void;
		onPatchTeam: (id: string, patch: Record<string, unknown>) => void;
		onSetTeamMembershipState: (id: string, state: ResourceState) => void;
		onSetModelTeamGrantState: (id: string, state: ResourceState) => void;
	} = $props();
</script>

<PageTitle title={'权限组'} subtitle={'自助注册用户会继承其所有启用权限组的模型访问权限。'} />
<div class="split">
	<section class="panel"><h2>创建权限组</h2><div class="form-grid"><label>名称<input bind:value={teamForm.name} /></label><label>备注<input bind:value={teamForm.notes} /></label><button type="button" onclick={onCreateTeam}>创建权限组</button></div></section>
	<section class="panel"><h2>把用户加入权限组</h2><div class="form-grid"><label>搜索用户<input bind:value={teamSubjectSearch} placeholder="输入姓名或工号" /></label><label>权限组<select bind:value={teamMembershipForm.team_id}><option value="">权限组</option>{#each teams as team}<option value={team.id}>{team.name}</option>{/each}</select></label><label>用户<select bind:value={teamMembershipForm.subject_id}><option value="">用户</option>{#each subjectOptions(teamSubjectSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label><label>角色<select bind:value={teamMembershipForm.role}>{#each managedRoles as role}<option value={role.value}>{role.label}</option>{/each}</select></label><button type="button" onclick={onCreateTeamMembership}>添加成员</button></div></section>
</div>
<section class="panel"><h2>给权限组授权模型</h2><div class="form-grid"><label>模型<select bind:value={modelTeamGrantForm.model_alias_id}><option value="">模型</option>{#each models as model}<option value={model.id}>{model.alias}</option>{/each}</select></label><label>权限组<select bind:value={modelTeamGrantForm.team_id}><option value="">权限组</option>{#each teams as team}<option value={team.id}>{team.name}</option>{/each}</select></label><button type="button" onclick={onCreateModelTeamGrant}>授权模型</button></div></section>
<section class="panel"><h2>权限组</h2><div class="table-wrap"><table><thead><tr><th>名称</th><th>状态</th><th>内置</th><th>备注</th><th>操作</th></tr></thead><tbody>{#each teams as team}<tr><td>{team.name}<br /><span class="muted">{short(team.id)}</span></td><td><StateBadge value={team.state} /></td><td><StateBadge value={team.is_builtin} tone="accent" /></td><td>{team.notes}</td><td><button class="secondary" type="button" onclick={() => onPatchTeam(team.id, { state: team.state === 'active' ? 'disabled' : 'active' })}>{team.state === 'active' ? '禁用' : '启用'}</button></td></tr>{/each}</tbody></table></div></section>
<section class="panel"><h2>成员关系</h2><div class="form-grid"><label>权限组<select bind:value={teamMembershipTeamFilter}><option value="">全部权限组</option>{#each teams as team}<option value={team.id}>{team.name}</option>{/each}</select></label><label>搜索用户<input bind:value={teamMembershipSubjectSearch} placeholder="姓名或工号" /></label><label>角色<input bind:value={teamMembershipRoleFilter} placeholder="member" /></label><label>状态<select bind:value={teamMembershipStateFilter}><option value="">全部状态</option><option value="active">启用</option><option value="disabled">停用</option></select></label></div><div class="table-wrap"><table><thead><tr><th>权限组</th><th>用户</th><th>角色</th><th>状态</th><th>操作</th></tr></thead><tbody>{#each teamMembershipPageRows as membership}<tr><td>{teamLabel(membership.team_id)}</td><td>{subjectLabel(membership.subject_id)}</td><td>{membership.role}</td><td><StateBadge value={membership.state} /></td><td><button class="secondary" type="button" onclick={() => onSetTeamMembershipState(membership.id, membership.state === 'active' ? 'disabled' : 'active')}>{membership.state === 'active' ? '禁用' : '启用'}</button></td></tr>{:else}<tr><td colspan="5" class="empty">没有匹配的成员关系。</td></tr>{/each}</tbody></table></div><Pagination total={teamMembershipRows.length} page={teamMembershipPage} size={PAGE_SIZE.defaultList} onPage={(page) => (teamMembershipPage = page)} /></section>
<section class="panel"><h2>模型授权</h2><div class="table-wrap"><table><thead><tr><th>模型</th><th>权限组</th><th>状态</th><th>操作</th></tr></thead><tbody>{#each modelTeamGrants as grant}<tr><td>{modelLabel(grant.model_alias_id)}</td><td>{teamLabel(grant.team_id)}</td><td><StateBadge value={grant.state} /></td><td><button class="secondary" type="button" onclick={() => onSetModelTeamGrantState(grant.id, grant.state === 'active' ? 'disabled' : 'active')}>{grant.state === 'active' ? '禁用' : '启用'}</button></td></tr>{/each}</tbody></table></div></section>
