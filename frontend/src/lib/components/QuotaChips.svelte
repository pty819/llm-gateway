<script lang="ts">
	import type { TeamTokenQuotaRow } from '$lib/api/types';
	import { fmtNumber } from '$lib/format';

	/** 分时段配额仪表:权限组表格行内,三个时间窗上限(设计稿 D3)。
	 * 上限对每个成员分别生效,因此这里只展示各窗上限;每个成员的实时
	 * 已用量在权限组抽屉的「Token 配额」页签里按成员展示。 */
	let { row }: { row: TeamTokenQuotaRow | undefined } = $props();

	const windows = $derived.by(() => {
		if (!row) return [];
		const defs: Array<{ key: 'morning' | 'afternoon' | 'evening'; label: string; limit: number | null }> = [
			{ key: 'morning', label: '上午', limit: row.morning_tokens },
			{ key: 'afternoon', label: '下午', limit: row.afternoon_tokens },
			{ key: 'evening', label: '晚上', limit: row.evening_tokens }
		];
		return defs;
	});
</script>

{#if !row || windows.every((w) => w.limit === null)}
	<span class="muted">不限</span>
{:else}
	<div class="quota-chips">
		{#each windows as win (win.key)}
			<div class="quota-chip">
				<span class="quota-win">{win.label}</span>
				{#if win.limit === null}
					<span class="quota-nums">∞</span>
				{:else}
					<span class="quota-nums">/ {fmtNumber(win.limit)}</span>
				{/if}
			</div>
		{/each}
		{#if row.state === 'disabled'}<span class="muted" style="font-size: 11px;">配额已停用</span>{/if}
	</div>
{/if}
