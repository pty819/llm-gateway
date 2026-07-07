<script lang="ts">
	import { onMount } from 'svelte';
	import type { AdminApiClient } from '$lib/api/client';
	import { downloadBlob as triggerDownload } from '$lib/api/client';
	import type { SkillDetail, SkillSummary, SkillTeamGrantSummary } from '$lib/api/types';
	import { createMarketController } from '$lib/state/market.svelte';
	import PageTitle from '$lib/components/PageTitle.svelte';
	import UploadSkillDialog from '$lib/components/UploadSkillDialog.svelte';
	import ReadmeDialog from '$lib/components/ReadmeDialog.svelte';
	import GrantsEditor from '$lib/components/GrantsEditor.svelte';
	import MarketTabs from '$lib/components/MarketTabs.svelte';
	import Pagination from '$lib/components/Pagination.svelte';

	let {
		client,
		teams
	}: {
		client: AdminApiClient;
		teams: { id: string; name: string }[];
	} = $props();

	const market = createMarketController<SkillSummary>({
		listMine: () => client.listMySkills(),
		browse: (params) => client.listBrowseSkills(params),
		like: (owner, slug) => client.likeSkill(owner, slug),
		unlike: (owner, slug) => client.unlikeSkill(owner, slug)
	});

	let uploading = $state(false);
	let selectedSlug = $state<string | null>(null);
	let grants = $state<SkillTeamGrantSummary[]>([]);
	let grantsLoading = $state(false);

	let readmeSkill = $state<SkillDetail | null>(null);
	let readmeOpen = $state(false);

	async function loadGrants(slug: string) {
		grantsLoading = true;
		try {
			const page = await client.listSkillGrants(slug);
			grants = page.items;
		} catch {
			grants = [];
		} finally {
			grantsLoading = false;
		}
	}

	async function selectSkill(slug: string) {
		selectedSlug = slug;
		await loadGrants(slug);
	}

	async function viewReadme(skill: SkillSummary) {
		try {
			const detail = await client.getSkillDetail(market.ownerOf(skill), skill.slug);
			market.syncLiked(skill, detail.liked_by_me);
			if (detail.readme) {
				readmeSkill = detail;
				readmeOpen = true;
			} else {
				market.browseError = '该 Skill 暂无 README。';
			}
		} catch (err) {
			market.browseError = err instanceof Error ? err.message : '加载详情失败。';
		}
	}

	async function doDownload(skill: SkillSummary) {
		try {
			const blob = await client.downloadSkill(market.ownerOf(skill), skill.slug, 'latest');
			triggerDownload(blob, `${skill.slug}.zip`);
		} catch (err) {
			market.browseError = err instanceof Error ? err.message : '下载失败。';
		}
	}

	onMount(() => {
		void market.loadMine();
	});
</script>

<PageTitle title={'Skill 市场'} subtitle={'管理你发布的 Skill 安装包及其对权限组的授权。'} />

<MarketTabs tab={market.tab} switchTab={market.switchTab} />

{#if market.tab === 'mine'}
	<section class="panel">
		<div class="section-head">
			<div>
				<h2>我的 Skill</h2>
				<p>上传 .zip 安装包后，可向权限组授权使用。</p>
			</div>
			<div class="actions">
				<button class="secondary" type="button" onclick={market.loadMine} disabled={market.loading}>
					{market.loading ? '加载中' : '刷新'}
				</button>
				<button type="button" onclick={() => (uploading = true)}>上传 Skill</button>
			</div>
		</div>
		{#if market.error}<p class="error">{market.error}</p>{/if}
		<div class="table-wrap">
			<table>
				<thead>
					<tr><th>Slug</th><th>名称</th><th>最新版本</th><th>状态</th></tr>
				</thead>
				<tbody>
					{#each market.items as skill (skill.id)}
						<tr
							class="clickable"
							aria-current={selectedSlug === skill.slug}
							onclick={() => selectSkill(skill.slug)}
						>
							<td><strong>{skill.slug}</strong></td>
							<td>{skill.name}{#if skill.summary}<br /><span class="muted">{skill.summary}</span>{/if}</td>
							<td>{skill.latest_version ?? '—'}</td>
							<td>
								<span class="badge {skill.state === 'active' ? 'success' : ''}">{skill.state === 'active' ? '已启用' : skill.state}</span>
							</td>
						</tr>
					{:else}
						<tr><td colspan="4" class="empty">{market.loading ? '加载中…' : '尚未发布任何 Skill。'}</td></tr>
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
					<p>当前 Skill：<code>{currentSlug}</code></p>
				</div>
				<button class="secondary" type="button" onclick={() => loadGrants(currentSlug)} disabled={grantsLoading}>
					{grantsLoading ? '加载中' : '刷新授权'}
				</button>
			</div>
			<GrantsEditor
				{grants}
				{teams}
				artifactLabel="Skill"
				onGrant={(teamId) => client.grantSkill(currentSlug, teamId)}
				onRevoke={(grantId) => client.revokeSkillGrant(currentSlug, grantId)}
				onChanged={() => loadGrants(currentSlug)}
			/>
		</section>
	{/if}
{:else}
	<section class="panel">
		<div class="section-head">
			<div>
				<h2>市场浏览</h2>
				<p>搜索并下载市场中的 Skill 安装包。</p>
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
					{#each market.browseItems as skill (skill.id)}
						<tr>
							<td><strong>{skill.slug}</strong></td>
							<td>{skill.name}{#if skill.summary}<br /><span class="muted">{skill.summary}</span>{/if}</td>
							<td>{skill.owner_name ?? skill.owner_subject_id}</td>
							<td>{skill.download_count}</td>
							<td>{skill.like_count}</td>
							<td class="ops">
								<button class="secondary" type="button" onclick={() => viewReadme(skill)}>查看README</button>
								<button class="secondary" type="button" onclick={() => doDownload(skill)}>下载</button>
								<button
									class="secondary"
									type="button"
									disabled={market.likeBusy === market.likeKey(skill)}
									onclick={() => market.toggleLike(skill)}
								>
									{market.isLiked(skill) ? '取消点赞' : '点赞'}
								</button>
							</td>
						</tr>
					{:else}
						<tr><td colspan="6" class="empty">{market.browseLoading ? '加载中…' : '没有匹配的 Skill。'}</td></tr>
					{/each}
				</tbody>
			</table>
		</div>
		<Pagination total={market.browseTotal} page={market.browsePage} size={market.browseSize} onPage={market.onPage} />
	</section>
{/if}

{#if uploading}
	<UploadSkillDialog
		{client}
		onClose={() => (uploading = false)}
		onUploaded={market.loadMine}
	/>
{/if}

{#if readmeOpen && readmeSkill}
	<ReadmeDialog
		readme={readmeSkill.readme ?? ''}
		title={`${readmeSkill.slug} · README`}
		onClose={() => {
			readmeOpen = false;
			readmeSkill = null;
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
</style>
