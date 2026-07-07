<script lang="ts">
	import type { Inventory, Project } from '$lib/api/types';
	import { PAGE_SIZE, short, subjectDisplay } from '$lib/admin-config';
	import StateBadge from '$lib/components/StateBadge.svelte';
	import PageTitle from '$lib/components/PageTitle.svelte';
	import Pagination from '$lib/components/Pagination.svelte';

	type ProjectForm = { name: string; owner_subject_id: string; notes: string };
	type MembershipForm = { project_id: string; subject_id: string; role: string };

	let {
		memberships,
		dropdownProjects,
		managedRoles,
		projectRows,
		projectPageRows,
		projectForm = $bindable(),
		membershipForm = $bindable(),
		projectOwnerSearch = $bindable(),
		projectMemberSearch = $bindable(),
		projectSearch = $bindable(),
		projectPage = $bindable(),
		subjectOptions,
		subjectLabel,
		projectLabel,
		onCreateProject,
		onCreateMembership,
		onPatchProject
	}: {
		memberships: Inventory['memberships'];
		dropdownProjects: Project[];
		managedRoles: { value: string; label: string }[];
		projectRows: Project[];
		projectPageRows: Project[];
		projectForm: ProjectForm;
		membershipForm: MembershipForm;
		projectOwnerSearch: string;
		projectMemberSearch: string;
		projectSearch: string;
		projectPage: number;
		subjectOptions: (query: string) => Inventory['subjects'];
		subjectLabel: (id: string | null | undefined) => string;
		projectLabel: (id: string | null | undefined) => string;
		onCreateProject: () => void;
		onCreateMembership: () => void;
		onPatchProject: (id: string, patch: Record<string, unknown>) => void;
	} = $props();
</script>

<PageTitle title={'项目'} subtitle={'用量归因和项目成员关系。'} />
<div class="split">
	<section class="panel">
		<h2>创建项目</h2>
		<div class="form-grid">
			<label>名称<input bind:value={projectForm.name} /></label>
			<label>搜索负责人<input bind:value={projectOwnerSearch} placeholder="输入姓名或工号" /></label>
			<label>负责人<select bind:value={projectForm.owner_subject_id}><option value="">无</option>{#each subjectOptions(projectOwnerSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label>
			<label>备注<input bind:value={projectForm.notes} /></label>
			<button type="button" onclick={onCreateProject}>创建项目</button>
		</div>
	</section>
	<section class="panel">
		<h2>添加项目成员</h2>
		<div class="form-grid">
			<label>项目<select bind:value={membershipForm.project_id}><option value="">项目</option>{#each dropdownProjects as project}<option value={project.id}>{project.name}</option>{/each}</select></label>
			<label>搜索用户<input bind:value={projectMemberSearch} placeholder="输入姓名或工号" /></label>
			<label>用户<select bind:value={membershipForm.subject_id}><option value="">用户</option>{#each subjectOptions(projectMemberSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label>
			<label>角色<select bind:value={membershipForm.role}>{#each managedRoles as role}<option value={role.value}>{role.label}</option>{/each}</select></label>
			<button type="button" onclick={onCreateMembership}>添加成员</button>
		</div>
	</section>
</div>
<section class="panel"><h2>项目</h2><div class="form-grid"><label>搜索项目<input bind:value={projectSearch} placeholder="项目名、负责人或备注" /></label></div><div class="table-wrap"><table><thead><tr><th>名称</th><th>负责人</th><th>状态</th><th>备注</th><th>操作</th></tr></thead><tbody>{#each projectPageRows as project}<tr><td>{project.name}<br /><span class="muted">{short(project.id)}</span></td><td>{subjectLabel(project.owner_subject_id)}</td><td><StateBadge value={project.state} /></td><td>{project.notes}</td><td><button class="secondary" type="button" onclick={() => onPatchProject(project.id, { notes: prompt('备注', project.notes ?? '') ?? project.notes })}>编辑备注</button></td></tr>{:else}<tr><td colspan="5" class="empty">没有匹配的项目。</td></tr>{/each}</tbody></table></div><Pagination total={projectRows.length} page={projectPage} size={PAGE_SIZE.defaultList} onPage={(page) => (projectPage = page)} /></section>
<section class="panel"><h2>项目成员</h2><div class="table-wrap"><table><thead><tr><th>项目</th><th>用户</th><th>角色</th></tr></thead><tbody>{#each memberships as membership}<tr><td>{projectLabel(membership.project_id)}</td><td>{subjectLabel(membership.subject_id)}</td><td>{membership.role}</td></tr>{/each}</tbody></table></div></section>
