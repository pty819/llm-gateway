<script lang="ts">
	import type { AdminApiClient } from '$lib/api/client';
	import { marketSlugPattern, bytesLabel } from '$lib/admin-config';

	let {
		client,
		onClose,
		onUploaded
	}: {
		client: AdminApiClient;
		onClose: () => void;
		onUploaded?: () => void;
	} = $props();

	const MAX_BYTES = 10 * 1024 * 1024; // 10 MiB

	let slug = $state('');
	let name = $state('');
	let version = $state('');
	let summary = $state('');
	let file = $state<File | null>(null);
	let saving = $state(false);
	let error = $state('');

	let slugError = $derived(slug && !marketSlugPattern.test(slug) ? 'slug 必须以小写字母开头，仅含小写字母、数字和连字符。' : '');

	function pickFile(event: Event) {
		const input = event.target as HTMLInputElement;
		file = input.files && input.files.length > 0 ? input.files[0] : null;
	}

	async function submit() {
		error = '';
		if (!slug || !marketSlugPattern.test(slug)) {
			error = 'slug 必须以小写字母开头，仅含小写字母、数字和连字符。';
			return;
		}
		if (!name.trim()) {
			error = '请填写 Skill 名称。';
			return;
		}
		if (!version.trim()) {
			error = '请填写版本号。';
			return;
		}
		if (!file) {
			error = '请选择 .zip 安装包。';
			return;
		}
		const lowerName = file.name.toLowerCase();
		if (!lowerName.endsWith('.zip')) {
			error = '安装包必须是 .zip 文件。';
			return;
		}
		if (file.size > MAX_BYTES) {
			error = `安装包过大（${bytesLabel(file.size)}），上限为 ${bytesLabel(MAX_BYTES)}。`;
			return;
		}
		saving = true;
		try {
			await client.uploadSkill(
				{
					slug: slug.trim(),
					name: name.trim(),
					version: version.trim(),
					summary: summary.trim() || undefined
				},
				file
			);
			onUploaded?.();
			onClose();
		} catch (err) {
			error = err instanceof Error ? err.message : '上传失败。';
		} finally {
			saving = false;
		}
	}
</script>

<div class="modal-backdrop" role="presentation">
	<section class="modal" aria-label="上传 Skill">
		<header>
			<h2>上传 Skill</h2>
			<p>将打包好的 .zip 安装包发布到 Skill 市场。</p>
		</header>
		<div class="form-grid">
			<label>
				Slug
				<input bind:value={slug} placeholder="my-skill" />
			</label>
			<label>
				名称
				<input bind:value={name} placeholder="我的 Skill" />
			</label>
			<label>
				版本
				<input bind:value={version} placeholder="0.1.0" />
			</label>
			<label>
				摘要
				<input bind:value={summary} placeholder="一句话描述" />
			</label>
			<label>
				安装包（.zip，上限 {bytesLabel(MAX_BYTES)}）
				<input type="file" accept=".zip" onchange={pickFile} />
			</label>
		</div>
		{#if file}<p class="muted">已选择：{file.name} · {bytesLabel(file.size)}</p>{/if}
		{#if slugError}<p class="muted">{slugError}</p>{/if}
		{#if error}<p class="error">{error}</p>{/if}
		<footer class="actions">
			<button type="button" onclick={submit} disabled={saving}>{saving ? '上传中' : '上传'}</button>
			<button class="secondary" type="button" onclick={onClose} disabled={saving}>取消</button>
		</footer>
	</section>
</div>
