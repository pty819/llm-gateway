<script lang="ts">
	import type { Inventory, ResourceState } from '$lib/api/types';
	import StateBadge from '$lib/components/StateBadge.svelte';
	import PageTitle from '$lib/components/PageTitle.svelte';

	type EntitlementForm = { model_alias_id: string; scope: string; scope_id: string };

	let {
		models,
		entitlements,
		entitlementForm = $bindable(),
		entitlementSubjectSearch = $bindable(),
		scopeOptions,
		subjectLabel,
		projectLabel,
		keyLabel,
		modelLabel,
		onCreate,
		onSetState
	}: {
		models: Inventory['models'];
		entitlements: Inventory['entitlements'];
		entitlementForm: EntitlementForm;
		entitlementSubjectSearch: string;
		scopeOptions: (scope: string, subjectQuery?: string) => { id: string; label: string }[];
		subjectLabel: (id: string | null | undefined) => string;
		projectLabel: (id: string | null | undefined) => string;
		keyLabel: (id: string | null | undefined) => string;
		modelLabel: (id: string | null | undefined) => string;
		onCreate: () => void;
		onSetState: (id: string, state: ResourceState) => void;
	} = $props();
</script>

<PageTitle title={'旧授权'} subtitle={'给项目、用户或单个网关密钥授予模型访问权限。'} />
<section class="panel"><h2>创建授权</h2><div class="form-grid"><label>模型<select bind:value={entitlementForm.model_alias_id}><option value="">模型</option>{#each models as model}<option value={model.id}>{model.alias}</option>{/each}</select></label><label>范围<select bind:value={entitlementForm.scope} onchange={() => (entitlementForm.scope_id = '')}><option value="project">项目</option><option value="subject">用户</option><option value="key">密钥</option></select></label>{#if entitlementForm.scope === 'subject'}<label>搜索用户<input bind:value={entitlementSubjectSearch} placeholder="输入姓名或工号" /></label>{/if}<label>授权对象<select bind:value={entitlementForm.scope_id}><option value="">对象</option>{#each scopeOptions(entitlementForm.scope, entitlementSubjectSearch) as option}<option value={option.id}>{option.label}</option>{/each}</select></label><button type="button" onclick={onCreate}>授权访问</button></div></section>
<section class="panel"><h2>授权</h2><div class="table-wrap"><table><thead><tr><th>模型</th><th>范围</th><th>状态</th><th>操作</th></tr></thead><tbody>{#each entitlements as entitlement}<tr><td>{modelLabel(entitlement.model_alias_id)}</td><td>{entitlement.project_id ? `项目: ${projectLabel(entitlement.project_id)}` : entitlement.subject_id ? `用户: ${subjectLabel(entitlement.subject_id)}` : `密钥: ${keyLabel(entitlement.gateway_key_id)}`}</td><td><StateBadge value={entitlement.state} /></td><td><button class="secondary" type="button" onclick={() => onSetState(entitlement.id, entitlement.state === 'active' ? 'disabled' : 'active')}>{entitlement.state === 'active' ? '禁用' : '启用'}</button></td></tr>{/each}</tbody></table></div></section>
