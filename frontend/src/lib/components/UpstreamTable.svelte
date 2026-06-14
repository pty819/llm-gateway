<script lang="ts">
	import type { Inventory, ResourceState, UpstreamHealth } from '$lib/api/types';
	import StateBadge from '$lib/components/StateBadge.svelte';
	import { errorMessage, short } from '$lib/admin-config';
	import { parseJsonObject } from '$lib/validators';

	let {
		rows,
		healthResults,
		modelLabel,
		onCheck,
		onState,
		onPatch,
		onDelete,
		onError
	}: {
		rows: Inventory['upstreams'];
		healthResults: Record<string, UpstreamHealth | string>;
		modelLabel: (id: string | null | undefined) => string;
		onCheck: (id: string) => void;
		onState: (id: string, state: ResourceState) => void;
		onPatch: (id: string, patch: Record<string, unknown>) => void;
		onDelete: (upstream: Inventory['upstreams'][number]) => void;
		onError?: (message: string) => void;
	} = $props();
</script>

<section class="panel">
	<h2>上游端点</h2>
	<div class="table-wrap">
		<table>
			<thead><tr><th>名称</th><th>模型</th><th>Base URL</th><th>Metrics URL</th><th>状态</th><th>密钥</th><th>健康</th><th>操作</th></tr></thead>
			<tbody>
				{#each rows as upstream}
					<tr>
						<td>{upstream.name}<br /><span class="muted">{short(upstream.id)}</span></td>
						<td>{modelLabel(upstream.model_alias_id)}</td>
						<td>{upstream.base_url}<br /><span class="muted">{upstream.health_path}</span></td>
						<td>{upstream.metrics_url ?? '自动推导'}</td>
						<td><StateBadge value={upstream.state} /></td>
						<td><StateBadge value={upstream.has_api_key} tone="accent" /></td>
						<td>
							{#if typeof healthResults[upstream.id] === 'string'}
								<span class="muted">{healthResults[upstream.id]}</span>
							{:else if healthResults[upstream.id]}
								<StateBadge value={(healthResults[upstream.id] as UpstreamHealth).health.status_code} tone={(healthResults[upstream.id] as UpstreamHealth).health.ok ? 'success' : 'danger'} />
							{:else}
								<span class="muted">未检查</span>
							{/if}
						</td>
						<td class="actions">
							<button class="secondary" type="button" onclick={() => onCheck(upstream.id)}>检查</button>
							<button class="secondary" type="button" onclick={() => {
								const next = prompt('新的 Base URL', upstream.base_url);
								if (next !== null) onPatch(upstream.id, { base_url: next });
							}}>改地址</button>
							<button class="secondary" type="button" onclick={() => {
								const next = prompt('健康检查路径', upstream.health_path);
								if (next !== null) onPatch(upstream.id, { health_path: next });
							}}>改健康路径</button>
							<button class="secondary" type="button" onclick={() => {
								const next = prompt('Metrics URL，留空则按 Base URL 自动推导 /metrics', upstream.metrics_url ?? '');
								if (next !== null) onPatch(upstream.id, { metrics_url: next.trim() || null });
							}}>改 metrics</button>
							<button class="secondary" type="button" onclick={() => {
								const next = prompt('上游名称', upstream.name);
								if (next !== null) onPatch(upstream.id, { name: next });
							}}>改名称</button>
							<button class="secondary" type="button" onclick={() => {
								const next = prompt('API key 引用', upstream.api_key_ref ?? '');
								if (next !== null) onPatch(upstream.id, { api_key_ref: next });
							}}>改 key 引用</button>
							<button class="secondary" type="button" onclick={() => {
								const next = prompt('新的 API key 明文，留空则不修改', '');
								if (next) onPatch(upstream.id, { api_key_value: next });
							}}>换 key</button>
							<button class="secondary" type="button" onclick={() => {
								const next = prompt('额外请求头 JSON', JSON.stringify(upstream.extra_headers, null, 2));
								if (next !== null) {
									try {
										onPatch(upstream.id, { extra_headers: parseJsonObject(next, '额外请求头') });
									} catch (error) {
										onError?.(errorMessage(error));
									}
								}
							}}>改请求头</button>
							<button class="secondary" type="button" onclick={() => onState(upstream.id, upstream.state === 'active' ? 'disabled' : 'active')}>{upstream.state === 'active' ? '禁用' : '启用'}</button>
							<button class="danger" type="button" onclick={() => onDelete(upstream)}>删除</button>
						</td>
					</tr>
				{:else}
					<tr><td colspan="8" class="empty">还没有配置上游端点。</td></tr>
				{/each}
			</tbody>
		</table>
	</div>
</section>
