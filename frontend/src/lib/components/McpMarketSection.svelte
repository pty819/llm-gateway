<script lang="ts">
	import { onMount } from 'svelte';
	import type { AdminApiClient } from '$lib/api/client';
	import type { McpDetail, McpSummary, McpTeamGrantSummary, McpVersionDetail } from '$lib/api/types';
	import { createMarketController } from '$lib/state/market.svelte';
	import PageTitle from '$lib/components/PageTitle.svelte';
	import CreateMcpDialog from '$lib/components/CreateMcpDialog.svelte';
	import GrantsEditor from '$lib/components/GrantsEditor.svelte';
	import MarketTabs from '$lib/components/MarketTabs.svelte';
	import ReadmeDialog from '$lib/components/ReadmeDialog.svelte';
	import Pagination from '$lib/components/Pagination.svelte';

	let {
		client,
		teams
	}: {
		client: AdminApiClient;
		teams: { id: string; name: string }[];
	} = $props();

	const market = createMarketController<McpSummary>({
		listMine: () => client.listMyMcps(),
		browse: (params) => client.listBrowseMcps(params),
		like: (owner, slug) => client.likeMcp(owner, slug),
		unlike: (owner, slug) => client.unlikeMcp(owner, slug)
	});

	let creating = $state(false);
	let selectedSlug = $state<string | null>(null);
	let grants = $state<McpTeamGrantSummary[]>([]);
	let grantsLoading = $state(false);

	let expandedKey = $state<string | null>(null);
	let detailCache = $state<Record<string, McpDetail>>({});
	let detailLoading = $state<string | null>(null);

	let readmeMcp = $state<McpDetail | null>(null);
	let readmeOpen = $state(false);

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

	async function toggleDetail(mcp: McpSummary) {
		const key = market.likeKey(mcp);
		if (expandedKey === key) {
			expandedKey = null;
			return;
		}
		expandedKey = key;
		if (!detailCache[key]) {
			detailLoading = key;
			try {
				const detail = await client.getMcpDetail(market.ownerOf(mcp), mcp.slug);
				detailCache = { ...detailCache, [key]: detail };
				market.syncLiked(mcp, detail.liked_by_me);
			} catch (err) {
				market.browseError = err instanceof Error ? err.message : '加载详情失败。';
				expandedKey = null;
			} finally {
				detailLoading = null;
			}
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
			const detail = await client.getMcpDetail(market.ownerOf(mcp), mcp.slug);
			market.syncLiked(mcp, detail.liked_by_me);
			if (detail.readme) {
				readmeMcp = detail;
				readmeOpen = true;
			} else {
				market.browseError = '该 MCP 暂无 README。';
			}
		} catch (err) {
			market.browseError = err instanceof Error ? err.message : '加载详情失败。';
		}
	}

	onMount(() => {
		void market.loadMine();
	});
</script>

<PageTitle title={'MCP 市场'} subtitle={'管理你发布的 MCP 服务器配置及其对权限组的授权。'} />

<MarketTabs tab={market.tab} switchTab={market.switchTab} />

{#if market.tab === 'mine'}
	<section class="panel">
		<div class="section-head">
			<div>
				<h2>我的 MCP</h2>
				<p>发布 MCP 服务器配置后，可向权限组授权使用。</p>
			</div>
			<div class="actions">
				<button class="secondary" type="button" onclick={market.loadMine} disabled={market.loading}>
					{market.loading ? '加载中' : '刷新'}
				</button>
				<button type="button" onclick={() => (creating = true)}>新建 MCP 配置</button>
			</div>
		</div>
		{#if market.error}<p class="error">{market.error}</p>{/if}
		<div class="table-wrap">
			<table>
				<thead>
					<tr><th>Slug</th><th>名称</th><th>最新版本</th><th>状态</th></tr>
				</thead>
				<tbody>
					{#each market.items as mcp (mcp.id)}
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
						<tr><td colspan="4" class="empty">{market.loading ? '加载中…' : '尚未发布任何 MCP。'}</td></tr>
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
			<GrantsEditor
				{grants}
				{teams}
				artifactLabel="MCP"
				onGrant={(teamId) => client.grantMcp(currentSlug, teamId)}
				onRevoke={(grantId) => client.revokeMcpGrant(currentSlug, grantId)}
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
			<button class="secondary" type="button" onclick={market.loadBrowse} disabled={market.browseLoading}>
				{market.browseLoading ? '加载中' : '刷新'}
			</button>
		</div>
		<div class="form-grid">
			<label>
				搜索
				<input
					type="search"
					bind:value={market.browseQ}
					placeholder="按名称或 slug 搜索"
					onkeydown={(e) => e.key === 'Enter' && market.onSearch()}
				/>
			</label>
			<label>
				排序
				<select bind:value={market.browseSort} onchange={market.onSortChange}>
					<option value="downloads">下载量</option>
					<option value="likes">点赞</option>
				</select>
			</label>
			<button type="button" onclick={market.onSearch}>搜索</button>
		</div>
		{#if market.browseError}<p class="error">{market.browseError}</p>{/if}
		<div class="table-wrap">
			<table>
				<thead>
					<tr><th>Slug</th><th>名称</th><th>所有者</th><th>下载量</th><th>点赞</th><th>操作</th></tr>
				</thead>
				<tbody>
					{#each market.browseItems as mcp (mcp.id)}
						{@const key = market.likeKey(mcp)}
						<tr>
							<td><strong>{mcp.slug}</strong></td>
							<td>{mcp.name}{#if mcp.summary}<br /><span class="muted">{mcp.summary}</span>{/if}</td>
							<td>{mcp.owner_name ?? mcp.owner_subject_id}</td>
							<td>{mcp.download_count}</td>
							<td>{mcp.like_count}</td>
							<td class="ops">
								<button class="secondary" type="button" onclick={() => viewReadme(mcp)}>查看README</button>
								<button class="secondary" type="button" onclick={() => toggleDetail(mcp)}>
									{expandedKey === key ? '收起' : '查看详情'}
								</button>
								<button
									class="secondary"
									type="button"
									disabled={market.likeBusy === key}
									onclick={() => market.toggleLike(mcp)}
								>
									{market.isLiked(mcp) ? '取消点赞' : '点赞'}
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
						<tr><td colspan="6" class="empty">{market.browseLoading ? '加载中…' : '没有匹配的 MCP。'}</td></tr>
					{/each}
				</tbody>
			</table>
		</div>
		<Pagination total={market.browseTotal} page={market.browsePage} size={market.browseSize} onPage={market.onPage} />
	</section>
{/if}

{#if creating}
	<CreateMcpDialog
		{client}
		onClose={() => (creating = false)}
		onPublished={market.loadMine}
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

<style>
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
