<script lang="ts">
	import { onMount } from 'svelte';
	import type { AdminApiClient } from '$lib/api/client';
	import type { McpDetail, McpSummary, McpTeamGrantSummary, McpVersionDetail } from '$lib/api/types';
	import PageTitle from '$lib/components/PageTitle.svelte';
	import CreateMcpDialog from '$lib/components/CreateMcpDialog.svelte';
	import McpGrantsEditor from '$lib/components/McpGrantsEditor.svelte';
	import ReadmeDialog from '$lib/components/ReadmeDialog.svelte';
	import Pagination from '$lib/components/Pagination.svelte';
	import { fmtNumber } from '$lib/format';
	import { Download, FileText, Heart, Plug } from 'lucide-svelte';

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

	let readmeMcp = $state<McpDetail | null>(null);
	let readmeOpen = $state(false);

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

	async function viewReadme(mcp: McpSummary) {
		try {
			const detail = await client.getMcpDetail(ownerKey(mcp), mcp.slug);
			const key = rowKey(mcp);
			const next = new Set(likedSet);
			if (detail.liked_by_me) next.add(key);
			else next.delete(key);
			likedSet = next;
			if (detail.readme) {
				readmeMcp = detail;
				readmeOpen = true;
			} else {
				browseError = '该 MCP 暂无 README。';
			}
		} catch (err) {
			browseError = err instanceof Error ? err.message : '加载详情失败。';
		}
	}

	// 当前展开详情的卡片(点击卡片切换)
	const expandedMcp = $derived(browseItems.find((m) => rowKey(m) === expandedKey) ?? null);
	const expandedDetail = $derived(expandedMcp ? detailCache[rowKey(expandedMcp)] : undefined);

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
		{#if browseLoading && browseItems.length === 0}
			<div class="market-grid">
				{#each Array(6) as _}
					<div class="market-card" aria-hidden="true"><div class="skeleton" style="height: 72px"></div><div class="skeleton" style="height: 16px"></div><div class="skeleton" style="height: 32px"></div></div>
				{/each}
			</div>
		{:else}
			<div class="market-grid">
				{#each browseItems as mcp (mcp.id)}
					{@const key = rowKey(mcp)}
					<article
						class="market-card"
						role="button"
						tabindex="0"
						aria-expanded={expandedKey === key}
						onclick={() => toggleDetail(mcp)}
						onkeydown={(event) => event.key === 'Enter' && toggleDetail(mcp)}
					>
						<div class="market-card-cover">{mcp.slug.slice(0, 2).toUpperCase()}</div>
						<div class="market-card-head">
							<strong>{mcp.name || mcp.slug}</strong>
							{#if expandedKey === key}
								<span class="badge accent">已展开</span>
							{:else if isLiked(mcp)}
								<span class="badge flow">已赞</span>
							{/if}
						</div>
						<p class="muted">{mcp.summary || mcp.slug}</p>
						<div class="market-card-foot">
							<span><Download size={12} /> {fmtNumber(mcp.download_count)}</span>
							<span><Heart size={12} /> {fmtNumber(mcp.like_count)}</span>
							<span>{ownerKey(mcp)}</span>
						</div>
						<div class="actions">
							<button class="secondary" type="button" onclick={(event) => { event.stopPropagation(); viewReadme(mcp); }}><FileText size={14} /> README</button>
							<button class="ghost" type="button" disabled={likeBusy === key} onclick={(event) => { event.stopPropagation(); toggleLike(mcp); }}><Heart size={14} /> {isLiked(mcp) ? '取消点赞' : '点赞'}</button>
						</div>
					</article>
				{:else}
					<div class="empty-state">
						<Plug size={28} />
						<strong>没有匹配的 MCP</strong>
						<p class="muted">换个关键词试试，或发布你自己的 MCP 配置。</p>
					</div>
				{/each}
			</div>
		{/if}
		<Pagination total={browseTotal} page={browsePage} size={browseSize} onPage={onPage} />
	</section>

	{#if expandedMcp}
		<section class="panel">
			<div class="section-head">
				<div>
					<h2>配置详情</h2>
					<p>{expandedMcp.name} · <code>{expandedMcp.slug}</code></p>
				</div>
				<button class="secondary" type="button" onclick={() => (expandedKey = null)}>收起</button>
			</div>
			{#if detailLoading === rowKey(expandedMcp)}
				<p class="muted">加载中…</p>
			{:else if expandedDetail}
				{@const ver = expandedDetail.latest ?? expandedDetail.versions[0]}
				<dl class="detail-list">
					<div class="detail-item"><dt>传输方式</dt><dd>{ver ? ver.transport : '—'}</dd></div>
					{#if ver?.command}<div class="detail-item"><dt>Command</dt><dd><code>{ver.command}</code></dd></div>{/if}
					{#if ver?.url}<div class="detail-item"><dt>URL</dt><dd><code>{ver.url}</code></dd></div>{/if}
					{#if ver && ver.args.length > 0}<div class="detail-item"><dt>Args</dt><dd><code>{ver.args.join(' ')}</code></dd></div>{/if}
					{#if ver && Object.keys(ver.env).length > 0}
						<div class="detail-item"><dt>Env</dt><dd>{#each Object.entries(ver.env) as [k, v]}<div><code>{k}</code> = <code>{v}</code></div>{/each}</dd></div>
					{/if}
					{#if ver && Object.keys(ver.headers).length > 0}
						<div class="detail-item"><dt>Headers</dt><dd>{#each Object.entries(ver.headers) as [k, v]}<div><code>{k}</code>: <code>{v}</code></div>{/each}</dd></div>
					{/if}
					{#if ver && ver.tools.length > 0}
						<div class="detail-item"><dt>Tools</dt><dd>{#each ver.tools as t}<div>{t.name ?? JSON.stringify(t)}</div>{/each}</dd></div>
					{/if}
					{#if expandedDetail.description}<div class="detail-item"><dt>描述</dt><dd>{expandedDetail.description}</dd></div>{/if}
					<div class="detail-item"><dt>版本</dt><dd>{ver ? versionLabel(ver) : '—'}{#if ver?.state} · {ver.state}{/if}</dd></div>
				</dl>
			{:else}
				<p class="muted">暂无详情。</p>
			{/if}
		</section>
	{/if}
{/if}

{#if creating}
	<CreateMcpDialog
		{client}
		onClose={() => (creating = false)}
		onPublished={loadMcps}
	/>
{/if}

{#if readmeOpen && readmeMcp}
	<ReadmeDialog
		readme={readmeMcp.readme ?? ''}
		title={`${readmeMcp.slug} · README`}
		onClose={() => {
			readmeOpen = false;
			readmeMcp = null;
		}}
	/>
{/if}

