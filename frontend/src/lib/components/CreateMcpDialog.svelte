<script lang="ts">
	import type { AdminApiClient } from '$lib/api/client';
	import type { McpConfigInput } from '$lib/api/types';
	import { marketSlugPattern } from '$lib/admin-config';

	let {
		client,
		onClose,
		onPublished
	}: {
		client: AdminApiClient;
		onClose: () => void;
		onPublished?: () => void;
	} = $props();

	let slug = $state('');
	let name = $state('');
	let version = $state('');
	let summary = $state('');
	let transport = $state<'stdio' | 'http' | 'sse'>('stdio');

	// stdio 字段
	let command = $state('');
	let argsText = $state('');
	let envText = $state('');

	// http / sse 字段
	let url = $state('');
	let headersText = $state('');

	// 工具
	let toolsText = $state('');

	let saving = $state(false);
	let error = $state('');

	let slugError = $derived(slug && !marketSlugPattern.test(slug) ? 'slug 必须以小写字母开头，仅含小写字母、数字和连字符。' : '');

	function parseKv(text: string, sep: string): Record<string, string> {
		const out: Record<string, string> = {};
		for (const line of text.split('\n')) {
			const t = line.trim();
			if (!t) continue;
			const idx = t.indexOf(sep);
			if (idx <= 0) continue;
			out[t.slice(0, idx).trim()] = t.slice(idx + 1).trim();
		}
		return out;
	}

	async function submit() {
		error = '';
		if (!slug || !marketSlugPattern.test(slug)) {
			error = 'slug 必须以小写字母开头，仅含小写字母、数字和连字符。';
			return;
		}
		if (!name.trim()) {
			error = '请填写 MCP 名称。';
			return;
		}
		if (!version.trim()) {
			error = '请填写版本号。';
			return;
		}
		if (transport === 'stdio' && !command.trim()) {
			error = 'stdio 传输需要填写 command。';
			return;
		}

		const config: McpConfigInput = { transport };
		if (transport === 'stdio') {
			config.command = command.trim();
			config.args = argsText
				.split('\n')
				.map((s) => s.trim())
				.filter(Boolean);
			config.env = parseKv(envText, '=');
		} else {
			config.url = url.trim() || null;
			config.headers = parseKv(headersText, ':');
		}
		config.tools = toolsText
			.split('\n')
			.map((s) => s.trim())
			.filter(Boolean)
			.map((toolName) => ({ name: toolName }));

		saving = true;
		try {
			await client.publishMcp(
				{
					slug: slug.trim(),
					name: name.trim(),
					version: version.trim(),
					summary: summary.trim() || undefined
				},
				config
			);
			onPublished?.();
			onClose();
		} catch (err) {
			error = err instanceof Error ? err.message : '发布失败。';
		} finally {
			saving = false;
		}
	}
</script>

<div class="modal-backdrop" role="presentation">
	<section class="modal" aria-label="新建 MCP 配置">
		<header>
			<h2>新建 MCP 配置</h2>
			<p>发布一个 MCP 服务器配置到 MCP 市场。</p>
		</header>
		<div class="form-grid">
			<label>
				Slug
				<input bind:value={slug} placeholder="my-mcp" />
			</label>
			<label>
				名称
				<input bind:value={name} placeholder="我的 MCP" />
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
				传输方式
				<select bind:value={transport}>
					<option value="stdio">stdio</option>
					<option value="http">http</option>
					<option value="sse">sse</option>
				</select>
			</label>
			{#if transport === 'stdio'}
				<label>
					Command
					<input bind:value={command} placeholder="npx" />
				</label>
				<label>
					Args（每行一个）
					<textarea bind:value={argsText} placeholder="-y&#10;@modelcontextprotocol/server-filesystem"></textarea>
				</label>
				<label>
					Env（每行 KEY=value）
					<textarea bind:value={envText} placeholder="API_KEY=secret"></textarea>
				</label>
			{:else}
				<label>
					URL
					<input bind:value={url} placeholder="https://example.com/mcp" />
				</label>
				<label>
					Headers（每行 KEY:value）
					<textarea bind:value={headersText} placeholder="Authorization:Bearer token"></textarea>
				</label>
			{/if}
			<label>
				Tools（每行一个工具名）
				<textarea bind:value={toolsText} placeholder="search&#10;read_file"></textarea>
			</label>
		</div>
		{#if slugError}<p class="muted">{slugError}</p>{/if}
		{#if error}<p class="error">{error}</p>{/if}
		<footer class="actions">
			<button type="button" onclick={submit} disabled={saving}>{saving ? '发布中' : '发布'}</button>
			<button class="secondary" type="button" onclick={onClose} disabled={saving}>取消</button>
		</footer>
	</section>
</div>
