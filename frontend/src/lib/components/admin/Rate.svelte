<script lang="ts">
	import type { Inventory, ResourceState } from '$lib/api/types';
	import { scopeLabel } from '$lib/admin-config';
	import StateBadge from '$lib/components/StateBadge.svelte';
	import PageTitle from '$lib/components/PageTitle.svelte';

	type RateForm = {
		scope: string;
		scope_id: string;
		requests_per_minute: string;
		concurrency_limit: string;
	};

	let {
		ratePolicies,
		rateForm = $bindable(),
		rateSubjectSearch = $bindable(),
		scopeOptions,
		subjectLabel,
		projectLabel,
		keyLabel,
		onCreate,
		onSetState
	}: {
		ratePolicies: Inventory['ratePolicies'];
		rateForm: RateForm;
		rateSubjectSearch: string;
		scopeOptions: (scope: string, subjectQuery?: string) => { id: string; label: string }[];
		subjectLabel: (id: string | null | undefined) => string;
		projectLabel: (id: string | null | undefined) => string;
		keyLabel: (id: string | null | undefined) => string;
		onCreate: () => void;
		onSetState: (id: string, state: ResourceState) => void;
	} = $props();
</script>

<PageTitle title={'限流策略'} subtitle={'基于数据库配置的每分钟请求数和并发限制。'} />
<section class="panel"><h2>创建限流策略</h2><p>实际生效限制会取密钥、用户、项目和环境默认值中的最小启用策略。</p><div class="form-grid"><label>范围<select bind:value={rateForm.scope} onchange={() => (rateForm.scope_id = '')}><option value="key">密钥</option><option value="subject">用户</option><option value="project">项目</option></select></label>{#if rateForm.scope === 'subject'}<label>搜索用户<input bind:value={rateSubjectSearch} placeholder="输入姓名或工号" /></label>{/if}<label>对象<select bind:value={rateForm.scope_id}><option value="">对象</option>{#each scopeOptions(rateForm.scope, rateSubjectSearch) as option}<option value={option.id}>{option.label}</option>{/each}</select></label><label>每分钟请求数<input type="number" min="0" bind:value={rateForm.requests_per_minute} /></label><label>并发限制<input type="number" min="0" bind:value={rateForm.concurrency_limit} /></label><button type="button" onclick={onCreate}>创建策略</button></div></section>
<section class="panel"><h2>策略</h2><div class="table-wrap"><table><thead><tr><th>范围</th><th>对象</th><th>RPM</th><th>并发</th><th>状态</th><th>操作</th></tr></thead><tbody>{#each ratePolicies as policy}<tr><td>{scopeLabel(policy.scope)}</td><td>{policy.scope === 'subject' ? subjectLabel(policy.scope_id) : policy.scope === 'project' ? projectLabel(policy.scope_id) : keyLabel(policy.scope_id)}</td><td>{policy.requests_per_minute ?? '继承'}</td><td>{policy.concurrency_limit ?? '继承'}</td><td><StateBadge value={policy.state} /></td><td><button class="secondary" type="button" onclick={() => onSetState(policy.id, policy.state === 'active' ? 'disabled' : 'active')}>{policy.state === 'active' ? '禁用' : '启用'}</button></td></tr>{/each}</tbody></table></div></section>
