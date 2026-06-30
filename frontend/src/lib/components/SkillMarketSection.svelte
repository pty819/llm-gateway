<script lang="ts">
	import { onMount } from 'svelte';
	import type { AdminApiClient } from '$lib/api/client';
	import { downloadBlob as triggerDownload } from '$lib/api/client';
	import type { SkillDetail, SkillSummary, SkillTeamGrantSummary } from '$lib/api/types';
	import PageTitle from '$lib/components/PageTitle.svelte';
	import UploadSkillDialog from '$lib/components/UploadSkillDialog.svelte';
	import ReadmeDialog from '$lib/components/ReadmeDialog.svelte';
	import ArtifactGrantsEditor from '$lib/components/ArtifactGrantsEditor.svelte';
	import Pagination from '$lib/components/Pagination.svelte';

	let {
		client,
		teams
	}: {
		client: AdminApiClient;
		teams: { id: string; name: string }[];
	} = $props();

	let tab = $state<'mine' | 'browse'>('mine');

	// —— 我的 Skill ——
	let skills = $state<SkillSummary[]>([]);
	let loading = $state(false);
	let error = $state('');
	let uploading = $state(false);
	let selectedSlug = $state<string | null>(null);
	let grants = $state<SkillTeamGrantSummary[]>([]);
	let grantsLoading = $state(false);

	async function loadSkills() {
		loading = true;
		error = '';
		try {
			const page = await client.listMySkills();
			skills = page.items;
		} catch (err) {
			error = err instanceof Error ? err.message : '加载 Skill 列表失败。';
		} finally {
			loading = false;
		}
	}

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

	// —— 市场浏览 ——
	let browseItems = $state<SkillSummary[]>([]);
	let browseTotal = $state(0);
	let browsePage = $state(1);
	let browseSize = $state(10);
	let browseSort = $state('downloads');
	let browseQ = $state('');
	let browseLoading = $state(false);
	let browseError = $state('');
	let browseLoaded = $state(false);

	let readmeSkill = $state<SkillDetail | null>(null);
	let readmeOpen = $state(false);
	let likeBusy = $state<string | null>(null);
	let likedSet = $state<Set<string>>(new Set());

	function likeKey(skill: SkillSummary): string {
		return `${ownerKey(skill)}/${skill.slug}`;
	}

	function isLiked(skill: SkillSummary): boolean {
		return likedSet.has(likeKey(skill));
	}

	async function loadBrowse() {
		browseLoading = true;
		browseError = '';
		try {
			const page = await client.listBrowseSkills({
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

	function ownerKey(skill: SkillSummary): string {
		return skill.owner_name ?? skill.owner_subject_id;
	}

	async function viewReadme(skill: SkillSummary) {
		try {
			const detail = await client.getSkillDetail(ownerKey(skill), skill.slug);
			const key = likeKey(skill);
			const next = new Set(likedSet);
			if (detail.liked_by_me) next.add(key);
			else next.delete(key);
			likedSet = next;
			if (detail.readme) {
				readmeSkill = detail;
				readmeOpen = true;
			} else {
				browseError = '该 Skill 暂无 README。';
			}
		} catch (err) {
			browseError = err instanceof Error ? err.message : '加载详情失败。';
		}
	}

	async function doDownload(skill: SkillSummary) {
		try {
			const blob = await client.downloadSkill(ownerKey(skill), skill.slug, 'latest');
			triggerDownload(blob, `${skill.slug}.zip`);
		} catch (err) {
			browseError = err instanceof Error ? err.message : '下载失败。';
		}
	}

	async function toggleLike(skill: SkillSummary) {
		const key = likeKey(skill);
		likeBusy = key;
		try {
			if (isLiked(skill)) {
				const res = await client.unlikeSkill(ownerKey(skill), skill.slug);
				skill.like_count = res.like_count;
				const next = new Set(likedSet);
				next.delete(key);
				likedSet = next;
			} else {
				const res = await client.likeSkill(ownerKey(skill), skill.slug);
				skill.like_count = res.like_count;
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

	onMount(() => {
		void loadSkills();
	});
</script>

<PageTitle title={'Skill 市场'} subtitle={'管理你发布的 Skill 安装包及其对权限组的授权。'} />

<div class="tabs">
	<button type="button" class:active={tab === 'mine'} onclick={() => switchTab('mine')}>我发布的</button>
	<button type="button" class:active={tab === 'browse'} onclick={() => switchTab('browse')}>市场浏览</button>
</div>

{#if tab === 'mine'}
	<section class="panel">
		<div class="section-head">
			<div>
				<h2>我的 Skill</h2>
				<p>上传 .zip 安装包后，可向权限组授权使用。</p>
			</div>
			<div class="actions">
				<button class="secondary" type="button" onclick={loadSkills} disabled={loading}>
					{loading ? '加载中' : '刷新'}
				</button>
				<button type="button" onclick={() => (uploading = true)}>上传 Skill</button>
			</div>
		</div>
		{#if error}<p class="error">{error}</p>{/if}
		<div class="table-wrap">
			<table>
				<thead>
					<tr><th>Slug</th><th>名称</th><th>最新版本</th><th>状态</th></tr>
				</thead>
				<tbody>
					{#each skills as skill (skill.id)}
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
						<tr><td colspan="4" class="empty">{loading ? '加载中…' : '尚未发布任何 Skill。'}</td></tr>
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
			<ArtifactGrantsEditor
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
				<p>搜索并下载市场中的 Skill 安装包。</p>
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
					{#each browseItems as skill (skill.id)}
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
									disabled={likeBusy === likeKey(skill)}
									onclick={() => toggleLike(skill)}
								>
									{isLiked(skill) ? '取消点赞' : '点赞'}
								</button>
							</td>
						</tr>
					{:else}
						<tr><td colspan="6" class="empty">{browseLoading ? '加载中…' : '没有匹配的 Skill。'}</td></tr>
					{/each}
				</tbody>
			</table>
		</div>
		<Pagination total={browseTotal} page={browsePage} size={browseSize} onPage={onPage} />
	</section>
{/if}

{#if uploading}
	<UploadSkillDialog
		{client}
		onClose={() => (uploading = false)}
		onUploaded={loadSkills}
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
</style>
