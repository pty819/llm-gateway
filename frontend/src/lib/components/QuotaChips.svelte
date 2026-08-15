<script lang="ts">
	import type { TeamTokenQuotaRow } from '$lib/api/types';
	import { fmtNumber } from '$lib/format';

	/** 分时段配额仪表:权限组表格行内,三个时间窗余量条(设计稿 D3)。
	 * 后端仅提供当前窗口的实时已用量(current_window_used),
	 * 因此当前窗高亮描边并显示 used/limit 进度,其余窗显示上限。 */
	let { row }: { row: TeamTokenQuotaRow | undefined } = $props();

	const windows = $derived.by(() => {
		if (!row) return [];
		const defs: Array<{ key: 'morning' | 'afternoon' | 'evening'; label: string; limit: number | null }> = [
			{ key: 'morning', label: '上午', limit: row.morning_tokens },
			{ key: 'afternoon', label: '下午', limit: row.afternoon_tokens },
			{ key: 'evening', label: '晚上', limit: row.evening_tokens }
		];
		return defs.map((def) => {
			const current = row.current_window === def.key && row.state !== 'disabled';
			const used = current ? row.current_window_used : null;
			let tone: '' | 'warn' | 'danger' = '';
			let pct = 0;
			if (current && used !== null && def.limit) {
				pct = Math.min(100, (used / def.limit) * 100);
				tone = used >= def.limit ? 'danger' : used >= def.limit * 0.8 ? 'warn' : '';
			}
			return { ...def, current, used, pct, tone };
		});
	});
</script>

{#if !row || windows.every((w) => w.limit === null)}
	<span class="muted">不限</span>
{:else}
	<div class="quota-chips">
		{#each windows as win (win.key)}
			<div class="quota-chip" class:current={win.current}>
				<span class="quota-win">{win.label}</span>
				{#if win.limit === null}
					<span class="quota-nums">∞</span>
				{:else}
					<div class="bar-track {win.tone}">
						<span style={`width: ${win.current ? win.pct : 0}%;`}></span>
					</div>
				{/if}
				<span class="quota-nums">
					{#if win.limit === null}
						∞
					{:else if win.used !== null}
						{fmtNumber(win.used)}/{fmtNumber(win.limit)}
					{:else}
						/{fmtNumber(win.limit)}
					{/if}
				</span>
			</div>
		{/each}
		{#if row.state === 'disabled'}<span class="muted" style="font-size: 11px;">配额已停用</span>{/if}
	</div>
{/if}
