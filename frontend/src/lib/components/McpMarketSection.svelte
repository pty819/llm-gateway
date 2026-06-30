<script lang="ts">
	import { onMount } from 'svelte';
	import type { AdminApiClient } from '$lib/api/client';
	import type { McpDetail, McpSummary, McpTeamGrantSummary, McpVersionDetail } from '$lib/api/types';
	import PageTitle from '$lib/components/PageTitle.svelte';
	import CreateMcpDialog from '$lib/components/CreateMcpDialog.svelte';
	import McpGrantsEditor from '$lib/components/McpGrantsEditor.svelte';
	import Pagination from '$lib/components/Pagination.svelte';

	let {
		client,
		teams
	}: {
		client: AdminApiClient;
		teams: { id: string; name: string }[];
	} = $props();

	let tab = $state<'mine' | 'browse'>('mine');

	// —— 我的 MCP ——
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

	// —— 市场浏览 ——
	let browseItems = $state<McpSummary[]>([]);
	let browseTotal = $state(0);
	let browsePage = $state(1);
	let browseSize = $state(10);
	let browseSort = $state('downloads');
	let browseQ = $state('');
	let browseLoading = $state(false);
	let browseError = $state('');
	let browseLoaded = $state(false);

	let expandedKey = $state<string | null>(null);
	let detailCache = $state<Record<string, McpDetail>>({});
	let detailLoading = $state<string | null>(null);
	let likeBusy = $state<string | null>(null);
	let likedSet = $state<Set<string>>(new Set());

	function ownerKey(mcp: McpSummary): string {
		return mcp.owner_name ?? mcp.owner_subject_id;
	}

	function rowKey(mcp: McpSummary): string {
		return `${ownerKey(mcp)}/${mcp.slug}`;
	}

	function isLiked(mcp: McpSummary): boolean {
		return likedSet.has(rowKey(mcp));
	}

	async function loadBrowse() {
		browseLoading = true;
		browseError = '';
		try {
			const page = await client.listBrowseMcps({
				q: browseQ.trim() || undefined,
				page: browsePage,
				size: browseSize,
				sort: browseSort
			});
			browseItems = page.items;
			browseTotal = page.total ?? page.items.length;
		} catch (err) {
			browseError = err instanceof Error ? err.message : '加载市场列表失败。';
			browseItems = [];
			browseTotal = 0;
		} finally {
			browseLoading = false;
			browseLoaded = true;
		}
	}

	async function switchTab(next: 'mine' | 'browse') {
		if (tab === next) return;
		tab = next;
		if (next === 'browse' && !browseLoaded) {
			await loadBrowse();
		}
	}

	function onSearch() {
		browsePage = 1;
		void loadBrowse();
	}

	function onSortChange() {
		browsePage = 1;
		void loadBrowse();
	}

	function onPage(p: number) {
		browsePage = p;
		void loadBrowse();
	}

	async function toggleDetail(mcp: McpSummary) {
		const key = rowKey(mcp);
		if (expandedKey === key) {
			expandedKey = null;
			return;
		}
		expandedKey = key;
		if (!detailCache[key]) {
			detailLoading = key;
			try {
				const detail = await client.getMcpDetail(ownerKey(mcp), mcp.slug);
				detailCache = { ...detailCache, [key]: detail };
				const next = new Set(likedSet);
				if (detail.liked_by_me) next.add(key);
				else next.delete(key);
				likedSet = next;
			} catch (err) {
				browseError = err instanceof Error ? err.message : '加载详情失败。';
				expandedKey = null;
			} finally {
				detailLoading = null;
			}
		}
	}

	async function toggleLike(mcp: McpSummary) {
		const key = rowKey(mcp);
		likeBusy = key;
		try {
			if (isLiked(mcp)) {
				const res = await client.unlikeMcp(ownerKey(mcp), mcp.slug);
				mcp.like_count = res.like_count;
				const next = new Set(likedSet);
				next.delete(key);
				likedSet = next;
			} else {
				const res = await client.likeMcp(ownerKey(mcp), mcp.slug);
				mcp.like_count = res.like_count;
				const next = new Set(likedSet);
				next.add(key);
				likedSet = next;
			}
		} catch (err) {
			browseError = err instanceof Error ? err.message : '操作失败。';
		} finally {
			likeBusy = null;
		}
	}

	function versionLabel(v: McpVersionDetail): string {
		const parts = [v.transport];
		if (v.command) parts.push(v.command);
		if (v.url) parts.push(v.url);
		return parts.join(' · ');
	}

	onMount(() => {
		void loadMcps();
	});
</script>

<PageTitle title={'MCP 市场'} subtitle={'管理你发布的 MCP 服务器配置及其对权限组的授权。'} />

<div class="tabs">
	<button type="button" class:active={tab === 'mine'} onclick={() => switchTab('mine')}>我发布的</button>
	<button type="button" class:active={tab === 'browse'} onclick={() => switchTab('browse')}>市场浏览</button>
</div>

{#if tab === 'mine'}
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
{:else}
	<section class="panel">
		<div class="section-head">
			<div>
				<h2>市场浏览</h2>
				<p>浏览并查看市场中的 MCP 服务器配置。</p>
			</div>
			<button class="secondary" type="button" onclick={loadBrowse} disabled={browseLoading}>
				{browseLoading ? '加载中' : '刷新'}
			</button>
		</div>
		<div class="form-grid">
			<label>
				搜索
				<input
					type="search"
					bind:value={browseQ}
					placeholder="按名称或 slug 搜索"
					onkeydown={(e) => e.key === 'Enter' && onSearch()}
				/>
			</label>
			<label>
				排序
				<select bind:value={browseSort} onchange={onSortChange}>
					<option value="downloads">下载量</option>
					<option value="likes">点赞</option>
				</select>
			</label>
			<button type="button" onclick={onSearch}>搜索</button>
		</div>
		{#if browseError}<p class="error">{browseError}</p>{/if}
		<div class="table-wrap">
			<table>
				<thead>
					<tr><th>Slug</th><th>名称</th><th>所有者</th><th>下载量</th><th>点赞</th><th>操作</th></tr>
				</thead>
				<tbody>
					{#each browseItems as mcp (mcp.id)}
						{@const key = rowKey(mcp)}
						<tr>
							<td><strong>{mcp.slug}</strong></td>
							<td>{mcp.name}{#if mcp.summary}<br /><span class="muted">{mcp.summary}</span>{/if}</td>
							<td>{mcp.owner_name ?? mcp.owner_subject_id}</td>
							<td>{mcp.download_count}</td>
							<td>{mcp.like_count}</td>
							<td class="ops">
								<button class="secondary" type="button" onclick={() => toggleDetail(mcp)}>
									{expandedKey === key ? '收起' : '查看详情'}
								</button>
								<button
									class="secondary"
									type="button"
									disabled={likeBusy === key}
									onclick={() => toggleLike(mcp)}
								>
									{isLiked(mcp) ? '取消点赞' : '点赞'}
								</button>
							</td>
						</tr>
						{#if expandedKey === key}
							<tr class="detail-row">
								<td colspan="6">
									{#if detailLoading === key}
										<p class="muted">加载中…</p>
									{:else if detailCache[key]}
										{@const detail = detailCache[key]}
										{@const ver = detail.latest ?? detail.versions[0]}
										<div class="detail-grid">
											<div><span class="lbl">传输方式</span> {ver ? ver.transport : '—'}</div>
											{#if ver?.command}<div><span class="lbl">Command</span> <code>{ver.command}</code></div>{/if}
											{#if ver?.url}<div><span class="lbl">URL</span> <code>{ver.url}</code></div>{/if}
											{#if ver && ver.args.length > 0}
												<div><span class="lbl">Args</span> <code>{ver.args.join(' ')}</code></div>
											{/if}
											{#if ver && Object.keys(ver.env).length > 0}
												<div><span class="lbl">Env</span>
													<ul class="kv">{#each Object.entries(ver.env) as [k, v]}<li><code>{k}</code> = <code>{v}</code></li>{/each}</ul>
												</div>
											{/if}
											{#if ver && Object.keys(ver.headers).length > 0}
												<div><span class="lbl">Headers</span>
													<ul class="kv">{#each Object.entries(ver.headers) as [k, v]}<li><code>{k}</code>: <code>{v}</code></li>{/each}</ul>
												</div>
											{/if}
											{#if ver && ver.tools.length > 0}
												<div><span class="lbl">Tools</span>
													<ul class="kv">{#each ver.tools as t}<li>{t.name ?? JSON.stringify(t)}</li>{/each}</ul>
												</div>
											{/if}
											{#if detail.description}<div><span class="lbl">描述</span> {detail.description}</div>{/if}
											<div><span class="lbl">版本</span> {ver ? versionLabel(ver) : '—'}{#if ver?.state} · {ver.state}{/if}</div>
										</div>
									{:else}
										<p class="muted">暂无详情。</p>
									{/if}
								</td>
							</tr>
						{/if}
					{:else}
						<tr><td colspan="6" class="empty">{browseLoading ? '加载中…' : '没有匹配的 MCP。'}</td></tr>
					{/each}
				</tbody>
			</table>
		</div>
		<Pagination total={browseTotal} page={browsePage} size={browseSize} onPage={onPage} />
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
	.tabs {
		display: flex;
		gap: 0.5rem;
		margin-bottom: 1rem;
	}

	.tabs button {
		padding: 0.45rem 0.9rem;
		border: 1px solid var(--border, #d8dce2);
		background: #fff;
		border-radius: 6px;
		cursor: pointer;
	}

	.tabs button.active {
		background: var(--accent-bg, #e7f0fb);
		border-color: var(--accent, #2b6cb0);
		font-weight: 600;
	}

	tr[aria-current='true'] {
		background: var(--accent-bg, #e7f0fb);
	}

	.ops {
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem;
	}

	.detail-row td {
		background: #f8f9fb;
		padding: 0.75rem 1rem;
	}

	.detail-grid {
		display: grid;
		gap: 0.4rem;
		font-size: 0.9rem;
	}

	.detail-grid .lbl {
		display: inline-block;
		min-width: 5rem;
		color: var(--text-muted);
		font-weight: 600;
	}

	.kv {
		margin: 0;
		padding-left: 1.2rem;
	}

	.kv li {
		line-height: 1.5;
	}
</style>
