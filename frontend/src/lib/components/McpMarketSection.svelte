<script lang="ts">
	import { onMount } from 'svelte';
	import type { AdminApiClient } from '$lib/api/client';
	import type { McpSummary, McpTeamGrantSummary } from '$lib/api/types';
	import PageTitle from '$lib/components/PageTitle.svelte';
	import CreateMcpDialog from '$lib/components/CreateMcpDialog.svelte';
	import McpGrantsEditor from '$lib/components/McpGrantsEditor.svelte';

	let {
		client,
		teams
	}: {
		client: AdminApiClient;
		teams: { id: string; name: string }[];
	} = $props();

	let mcps = $state<McpSummary[]>([]);
	let loading = $state(false);
	let error = $state('');
	let creating = $state(false);
	let selectedSlug = $state<string | null>(null);
	let grants = $state<McpTeamGrantSummary[]>([]);
	let grantsLoading = $state(false);

	async function loadMcps() {
		loading = true;
		error = '';
		try {
			const page = await client.listMyMcps();
			mcps = page.items;
		} catch (err) {
			error = err instanceof Error ? err.message : '加载 MCP 列表失败。';
		} finally {
			loading = false;
		}
	}

	async function loadGrants(slug: string) {
		grantsLoading = true;
		try {
			const page = await client.listMcpGrants(slug);
			grants = page.items;
		} catch {
			grants = [];
		} finally {
			grantsLoading = false;
		}
	}

	async function selectMcp(slug: string) {
		selectedSlug = slug;
		await loadGrants(slug);
	}

	onMount(() => {
		void loadMcps();
	});
</script>

<PageTitle title={'MCP 市场'} subtitle={'管理你发布的 MCP 服务器配置及其对权限组的授权。'} />

<section class="panel">
	<div class="section-head">
		<div>
			<h2>我的 MCP</h2>
			<p>发布 MCP 服务器配置后，可向权限组授权使用。</p>
		</div>
		<div class="actions">
			<button class="secondary" type="button" onclick={loadMcps} disabled={loading}>
				{loading ? '加载中' : '刷新'}
			</button>
			<button type="button" onclick={() => (creating = true)}>新建 MCP 配置</button>
		</div>
	</div>
	{#if error}<p class="error">{error}</p>{/if}
	<div class="table-wrap">
		<table>
			<thead>
				<tr><th>Slug</th><th>名称</th><th>最新版本</th><th>状态</th></tr>
			</thead>
			<tbody>
				{#each mcps as mcp (mcp.id)}
					<tr
						class="clickable"
						aria-current={selectedSlug === mcp.slug}
						onclick={() => selectMcp(mcp.slug)}
					>
						<td><strong>{mcp.slug}</strong></td>
						<td>{mcp.name}{#if mcp.summary}<br /><span class="muted">{mcp.summary}</span>{/if}</td>
						<td>{mcp.latest_version ?? '—'}</td>
						<td>
							<span class="badge {mcp.state === 'active' ? 'success' : ''}">{mcp.state === 'active' ? '已启用' : mcp.state}</span>
						</td>
					</tr>
				{:else}
					<tr><td colspan="4" class="empty">{loading ? '加载中…' : '尚未发布任何 MCP。'}</td></tr>
				{/each}
			</tbody>
		</table>
	</div>
</section>

{#if selectedSlug}
	{@const currentSlug = selectedSlug}
	<section class="panel">
		<div class="section-head">
			<div>
				<h2>授权管理</h2>
				<p>当前 MCP：<code>{currentSlug}</code></p>
			</div>
			<button class="secondary" type="button" onclick={() => loadGrants(currentSlug)} disabled={grantsLoading}>
				{grantsLoading ? '加载中' : '刷新授权'}
			</button>
		</div>
		<McpGrantsEditor
			{client}
			slug={currentSlug}
			{grants}
			{teams}
			onChanged={() => loadGrants(currentSlug)}
		/>
	</section>
{/if}

{#if creating}
	<CreateMcpDialog
		{client}
		onClose={() => (creating = false)}
		onPublished={loadMcps}
	/>
{/if}

<style>
	/* 仅为本组件内对全局 .clickable 行的选中态补充高亮，避免与全局样式冲突 */
	tr[aria-current='true'] {
		background: var(--accent-bg, #e7f0fb);
	}
</style>
