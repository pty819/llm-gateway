<script lang="ts">
	import type { Inventory, Project, ResourceState } from '$lib/api/types';
	import { PAGE_SIZE, subjectDisplay } from '$lib/admin-config';
	import StateBadge from '$lib/components/StateBadge.svelte';
	import PageTitle from '$lib/components/PageTitle.svelte';
	import Pagination from '$lib/components/Pagination.svelte';

	type KeyForm = { subject_id: string; project_id: string; name: string };

	let {
		projects,
		keyRows,
		keyPageRows,
		keyForm = $bindable(),
		keySubjectSearch = $bindable(),
		keyListSubjectSearch = $bindable(),
		keyProjectFilter = $bindable(),
		keyStateFilter = $bindable(),
		keyPage = $bindable(),
		dropdownProjects,
		subjectOptions,
		subjectLabel,
		projectLabel,
		onIssue,
		onSetState
	}: {
		projects: Inventory['projects'];
		keyRows: Inventory['keys'];
		keyPageRows: Inventory['keys'];
		keyForm: KeyForm;
		keySubjectSearch: string;
		keyListSubjectSearch: string;
		keyProjectFilter: string;
		keyStateFilter: string;
		keyPage: number;
		dropdownProjects: Project[];
		subjectOptions: (query: string) => Inventory['subjects'];
		subjectLabel: (id: string | null | undefined) => string;
		projectLabel: (id: string | null | undefined) => string;
		onIssue: () => void;
		onSetState: (id: string, state: ResourceState) => void;
	} = $props();
</script>

<PageTitle title={'网关密钥'} subtitle={'签发、轮换和停用网关管理的密钥。'} />
<section class="panel"><h2>签发密钥</h2><div class="form-grid"><label>搜索用户<input bind:value={keySubjectSearch} placeholder="输入姓名或工号" /></label><label>用户<select bind:value={keyForm.subject_id}><option value="">用户</option>{#each subjectOptions(keySubjectSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label><label>项目<select bind:value={keyForm.project_id}><option value="">项目</option>{#each dropdownProjects as project}<option value={project.id}>{project.name}</option>{/each}</select></label><label>名称<input bind:value={keyForm.name} /></label><button type="button" onclick={onIssue}>签发密钥</button></div></section>
<section class="panel"><h2>密钥</h2><div class="form-grid"><label>搜索用户/密钥<input bind:value={keyListSubjectSearch} placeholder="姓名、工号、密钥名或前缀" /></label><label>项目<select bind:value={keyProjectFilter}><option value="">全部项目</option>{#each projects as project}<option value={project.id}>{project.name}</option>{/each}</select></label><label>状态<select bind:value={keyStateFilter}><option value="">全部状态</option><option value="active">启用</option><option value="disabled">停用</option></select></label></div><div class="table-wrap"><table><thead><tr><th>名称</th><th>前缀</th><th>用户</th><th>项目</th><th>状态</th><th>操作</th></tr></thead><tbody>{#each keyPageRows as key}<tr><td>{key.name}</td><td><code>{key.key_prefix}</code></td><td>{subjectLabel(key.subject_id)}</td><td>{projectLabel(key.project_id)}</td><td><StateBadge value={key.state} /></td><td><button class="secondary" type="button" onclick={() => onSetState(key.id, key.state === 'active' ? 'disabled' : 'active')}>{key.state === 'active' ? '禁用' : '启用'}</button></td></tr>{:else}<tr><td colspan="6" class="empty">没有匹配的密钥。</td></tr>{/each}</tbody></table></div><Pagination total={keyRows.length} page={keyPage} size={PAGE_SIZE.defaultList} onPage={(page) => (keyPage = page)} /></section>
