<script lang="ts">
	import type { Inventory, SubjectType } from '$lib/api/types';
	import { PAGE_SIZE, short, subjectTypeLabel, subjectDisplay } from '$lib/admin-config';
	import StateBadge from '$lib/components/StateBadge.svelte';
	import PageTitle from '$lib/components/PageTitle.svelte';
	import Pagination from '$lib/components/Pagination.svelte';

	type SubjectForm = {
		name: string;
		login_username: string;
		password: string;
		type: SubjectType;
		notes: string;
	};
	type SubjectPasswordForm = { subject_id: string; new_password: string };

	let {
		subjectRows,
		subjectPageRows,
		subjectForm = $bindable(),
		subjectPasswordForm = $bindable(),
		subjectSearch = $bindable(),
		subjectPasswordSearch = $bindable(),
		subjectPage = $bindable(),
		subjectOptions,
		onCreate,
		onResetPassword,
		onPatch,
		onSetState,
		onDelete
	}: {
		subjectRows: Inventory['subjects'];
		subjectPageRows: Inventory['subjects'];
		subjectForm: SubjectForm;
		subjectPasswordForm: SubjectPasswordForm;
		subjectSearch: string;
		subjectPasswordSearch: string;
		subjectPage: number;
		subjectOptions: (query: string) => Inventory['subjects'];
		onCreate: () => void;
		onResetPassword: () => void;
		onPatch: (id: string, patch: Record<string, unknown>) => void;
		onSetState: (id: string, state: 'active' | 'disabled') => void;
		onDelete: (subject: Inventory['subjects'][number]) => void;
	} = $props();
</script>

<PageTitle title={'用户'} subtitle={'由网关管理的人类用户和服务账号。'} />
<section class="panel">
	<h2>创建用户</h2>
	<div class="form-grid">
		<label>真实姓名<input bind:value={subjectForm.name} /></label>
		<label>工号<input bind:value={subjectForm.login_username} placeholder="l00014624" /></label>
		<label>初始密码<input type="password" bind:value={subjectForm.password} /></label>
		<label>类型<select bind:value={subjectForm.type}><option value="user">用户</option><option value="service">服务账号</option></select></label>
		<label>备注<input bind:value={subjectForm.notes} /></label>
		<button type="button" onclick={onCreate}>创建用户</button>
	</div>
</section>
<section class="panel">
	<h2>重置用户密码</h2>
	<div class="form-grid">
		<label>搜索用户<input bind:value={subjectPasswordSearch} placeholder="输入姓名或工号" /></label>
		<label>用户<select bind:value={subjectPasswordForm.subject_id}><option value="">选择用户</option>{#each subjectOptions(subjectPasswordSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label>
		<label>新密码<input type="password" bind:value={subjectPasswordForm.new_password} /></label>
		<button type="button" onclick={onResetPassword}>重置密码</button>
	</div>
</section>
<section class="panel">
	<h2>用户</h2>
	<div class="form-grid"><label>搜索用户<input bind:value={subjectSearch} placeholder="输入姓名、工号或备注" /></label></div>
	<div class="table-wrap"><table><thead><tr><th>真实姓名</th><th>工号</th><th>类型</th><th>状态</th><th>备注</th><th>操作</th></tr></thead><tbody>{#each subjectPageRows as subject}<tr><td>{subject.name}<br /><span class="muted">{short(subject.id)}</span></td><td>{subject.login_username ?? '无'}</td><td>{subjectTypeLabel(subject.type)}</td><td><StateBadge value={subject.state} /></td><td>{subject.notes}</td><td class="actions"><button class="secondary" type="button" onclick={() => onPatch(subject.id, { name: prompt('真实姓名', subject.name) ?? subject.name })}>编辑姓名</button><button class="secondary" type="button" onclick={() => onPatch(subject.id, { notes: prompt('备注', subject.notes ?? '') ?? subject.notes })}>编辑备注</button><button class="secondary" type="button" onclick={() => onSetState(subject.id, subject.state === 'active' ? 'disabled' : 'active')}>{subject.state === 'active' ? '禁用' : '启用'}</button><button class="danger" type="button" onclick={() => onDelete(subject)}>删除</button></td></tr>{:else}<tr><td colspan="6" class="empty">没有匹配的用户。</td></tr>{/each}</tbody></table></div>
	<Pagination total={subjectRows.length} page={subjectPage} size={PAGE_SIZE.defaultList} onPage={(page) => (subjectPage = page)} />
</section>
