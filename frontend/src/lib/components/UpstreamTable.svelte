<script lang="ts">
	import type { Inventory, ResourceState, UpstreamHealth } from '$lib/api/types';
	import StateBadge from '$lib/components/StateBadge.svelte';
	import Switch from '$lib/components/Switch.svelte';
	import RowMenu from '$lib/components/RowMenu.svelte';
	import Drawer from '$lib/components/Drawer.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { errorMessage, short } from '$lib/admin-config';
	import { parseJsonObject } from '$lib/validators';

	/** 上游端点表(设计稿 P2/P3):行内 Switch 启停 + kebab 菜单,编辑统一走抽屉,替代 10 个行内按钮与 prompt。 */
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

	type UpstreamRow = Inventory['upstreams'][number];

	let editor = $state<{
		id: string;
		name: string;
		base_url: string;
		health_path: string;
		metrics_url: string;
		api_key_ref: string;
		api_key_value: string;
		extra_headers: string;
	} | null>(null);

	function openEditor(upstream: UpstreamRow) {
		editor = {
			id: upstream.id,
			name: upstream.name,
			base_url: upstream.base_url,
			health_path: upstream.health_path,
			metrics_url: upstream.metrics_url ?? '',
			api_key_ref: upstream.api_key_ref ?? '',
			api_key_value: '',
			extra_headers: JSON.stringify(upstream.extra_headers, null, 2)
		};
	}

	function saveEditor() {
		if (!editor) return;
		if (!editor.base_url.trim()) {
			onError?.('Base URL 不能为空。');
			return;
		}
		if (!editor.health_path.startsWith('/')) {
			onError?.('健康检查路径必须以 / 开头。');
			return;
		}
		const extraHeaders: Record<string, string> = {};
		try {
			const parsed = parseJsonObject(editor.extra_headers, '额外请求头');
			for (const [key, item] of Object.entries(parsed)) {
				if (typeof item !== 'string') {
					onError?.('额外请求头的值必须全部是字符串。');
					return;
				}
				extraHeaders[key] = item;
			}
		} catch (error) {
			onError?.(errorMessage(error));
			return;
		}
		const target = editor.id;
		const patch: Record<string, unknown> = {
			name: editor.name,
			base_url: editor.base_url,
			health_path: editor.health_path,
			metrics_url: editor.metrics_url.trim() || null,
			api_key_ref: editor.api_key_ref,
			extra_headers: extraHeaders
		};
		if (editor.api_key_value) patch.api_key_value = editor.api_key_value;
		onPatch(target, patch);
		editor = null;
	}
</script>

<section class="panel flush">
	<div class="table-wrap">
		<table>
			<thead><tr><th>名称</th><th>模型</th><th>Base URL</th><th>Metrics</th><th>密钥</th><th>健康</th><th>状态</th><th></th></tr></thead>
			<tbody>
				{#each rows as upstream}
					<tr>
						<td><strong>{upstream.name}</strong><br /><span class="sub mono">{short(upstream.id)}</span></td>
						<td>{upstream.model_alias ?? modelLabel(upstream.model_alias_id)}</td>
						<td class="ellipsis">{upstream.base_url}<br /><span class="sub">{upstream.health_path}</span></td>
						<td class="ellipsis">{upstream.metrics_url ?? '自动推导'}</td>
						<td><StateBadge value={upstream.has_api_key} tone="accent" /></td>
						<td class="nowrap">
							{#if typeof healthResults[upstream.id] === 'string'}
								<span class="muted">{healthResults[upstream.id]}</span>
							{:else if healthResults[upstream.id]}
								<StateBadge value={(healthResults[upstream.id] as UpstreamHealth).health.status_code} tone={(healthResults[upstream.id] as UpstreamHealth).health.ok ? 'success' : 'danger'} />
							{:else}
								<span class="muted">未检查</span>
							{/if}
						</td>
						<td>
							<Switch checked={upstream.state === 'active'} label="切换上游状态" onToggle={() => onState(upstream.id, upstream.state === 'active' ? 'disabled' : 'active')} />
						</td>
						<td class="nowrap">
							<RowMenu
								label="上游操作"
								items={[
									{ label: '检查健康', onclick: () => onCheck(upstream.id) },
									{ label: '编辑端点', onclick: () => openEditor(upstream) },
									{ label: '删除', danger: true, onclick: () => onDelete(upstream) }
								]}
							/>
						</td>
					</tr>
				{:else}
					<tr><td colspan="8"><EmptyState title="还没有配置上游端点" hint="在右上角「创建上游」接入第一个推理副本。" /></td></tr>
				{/each}
			</tbody>
		</table>
	</div>
</section>

<Drawer open={editor !== null} title="编辑上游端点" subtitle={editor?.name ?? ''} wide onClose={() => (editor = null)}>
	{#if editor}
		<div class="drawer-form">
			<label>名称<input bind:value={editor.name} /></label>
			<label>健康检查路径<input bind:value={editor.health_path} /></label>
			<label class="span-2">Base URL<input bind:value={editor.base_url} /></label>
			<label class="span-2">Metrics URL<input bind:value={editor.metrics_url} placeholder="留空则按 Base URL 自动推导 /metrics" /></label>
			<label>API key 引用<input bind:value={editor.api_key_ref} /></label>
			<label>API key 明文<input type="password" bind:value={editor.api_key_value} placeholder="留空则不修改" /></label>
			<label class="span-2">额外请求头<textarea bind:value={editor.extra_headers}></textarea></label>
		</div>
	{/if}
	{#snippet footer()}
		<button class="secondary" type="button" onclick={() => (editor = null)}>取消</button>
		<button type="button" onclick={saveEditor}>保存</button>
	{/snippet}
</Drawer>
