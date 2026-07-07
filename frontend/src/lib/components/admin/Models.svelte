<script lang="ts">
	import type { Inventory, IPPolicyMode } from '$lib/api/types';
	import StateBadge from '$lib/components/StateBadge.svelte';
	import PageTitle from '$lib/components/PageTitle.svelte';

	type ModelForm = {
		alias: string;
		upstream_model_name: string;
		supports_streaming: boolean;
		supports_tools: boolean;
		supports_reasoning: boolean;
		sticky_ttl_seconds: number;
		ip_policy_mode: IPPolicyMode;
		ip_allowlist_cidrs: string;
		notes: string;
	};

	let {
		models,
		modelForm = $bindable(),
		onCreate,
		onEditCidrs,
		onPatch,
		onDelete
	}: {
		models: Inventory['models'];
		modelForm: ModelForm;
		onCreate: () => void;
		onEditCidrs: (model: Inventory['models'][number]) => void;
		onPatch: (id: string, patch: Record<string, unknown>) => void;
		onDelete: (model: Inventory['models'][number]) => void;
	} = $props();
</script>

<PageTitle title={'模型别名'} subtitle={'配置下游模型名称、上游模型映射、能力标记和模型级 IP 策略。'} />
<section class="panel">
	<h2>创建模型别名</h2>
	<div class="form-grid">
		<label>别名<input bind:value={modelForm.alias} placeholder="dev-model" /></label>
		<label>上游模型名<input bind:value={modelForm.upstream_model_name} /></label>
		<label>粘性生命周期秒数<input type="number" min="1" max="86400" bind:value={modelForm.sticky_ttl_seconds} /></label>
		<label>IP 策略<select bind:value={modelForm.ip_policy_mode}><option value="all_pass">全部放行</option><option value="allowlist">白名单</option></select></label>
		<label>CIDRs<textarea bind:value={modelForm.ip_allowlist_cidrs} placeholder="10.0.0.0/8"></textarea></label>
		<label>备注<input bind:value={modelForm.notes} /></label>
	</div>
	<div class="actions">
		<label style="display:flex; width:auto; align-items:center;"><input type="checkbox" bind:checked={modelForm.supports_streaming} style="width:auto;" /> Streaming</label>
		<label style="display:flex; width:auto; align-items:center;"><input type="checkbox" bind:checked={modelForm.supports_tools} style="width:auto;" /> Tools</label>
		<label style="display:flex; width:auto; align-items:center;"><input type="checkbox" bind:checked={modelForm.supports_reasoning} style="width:auto;" /> Reasoning</label>
		<button type="button" onclick={onCreate}>创建别名</button>
	</div>
</section>
<section class="panel">
	<h2>模型别名</h2>
	<div class="table-wrap">
		<table>
			<thead><tr><th>别名</th><th>上游模型</th><th>状态</th><th>粘性 TTL</th><th>IP 策略</th><th>Streaming</th><th>Tools</th><th>Reasoning</th><th>操作</th></tr></thead>
			<tbody>
				{#each models as model}
					<tr>
						<td><strong>{model.alias}</strong><br /><span class="muted">{model.upstream_model_name}</span></td>
						<td><span class="badge">OpenAI</span><br /><span class="muted">{model.upstream_model_name}</span></td>
						<td><StateBadge value={model.state} /></td>
						<td>{model.sticky_ttl_seconds}s</td>
						<td><StateBadge value={model.ip_policy_mode} /><br /><span class="muted">{model.ip_allowlist_cidrs.join(', ') || '未配置 CIDR'}</span></td>
						<td><StateBadge value={model.supports_streaming} tone="accent" /></td>
						<td><StateBadge value={model.supports_tools} tone="accent" /></td>
						<td><StateBadge value={model.supports_reasoning} tone="accent" /></td>
						<td class="actions"><button class="secondary" type="button" onclick={() => onEditCidrs(model)}>编辑 CIDR</button><button class="secondary" type="button" onclick={() => {
							const next = prompt('粘性生命周期秒数', String(model.sticky_ttl_seconds));
							if (next !== null) onPatch(model.id, { sticky_ttl_seconds: Number(next) });
						}}>编辑 TTL</button><button class="secondary" type="button" onclick={() => onPatch(model.id, { state: model.state === 'active' ? 'disabled' : 'active' })}>{model.state === 'active' ? '禁用' : '启用'}</button><button class="danger" type="button" onclick={() => onDelete(model)}>删除</button></td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</section>
