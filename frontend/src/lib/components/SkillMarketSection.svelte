<script lang="ts">
	import { onMount } from 'svelte';
	import type { AdminApiClient } from '$lib/api/client';
	import type { SkillSummary, SkillTeamGrantSummary } from '$lib/api/types';
	import PageTitle from '$lib/components/PageTitle.svelte';
	import UploadSkillDialog from '$lib/components/UploadSkillDialog.svelte';
	import ArtifactGrantsEditor from '$lib/components/ArtifactGrantsEditor.svelte';

	let {
		client,
		teams
	}: {
		client: AdminApiClient;
		teams: { id: string; name: string }[];
	} = $props();

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

	onMount(() => {
		void loadSkills();
	});
</script>

<PageTitle title={'Skill 市场'} subtitle={'管理你发布的 Skill 安装包及其对权限组的授权。'} />

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

{#if uploading}
	<UploadSkillDialog
		{client}
		onClose={() => (uploading = false)}
		onUploaded={loadSkills}
	/>
{/if}

<style>
	/* 仅为本组件内对全局 .clickable 行的选中态补充高亮，避免与全局样式冲突 */
	tr[aria-current='true'] {
		background: var(--accent-bg, #e7f0fb);
	}
</style>
