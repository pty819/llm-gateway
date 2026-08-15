<script lang="ts">
	import type { AnalyticsBucketRow } from '$lib/api/types';
	import { fmtNumber } from '$lib/format';
	import { parseServerUtcIso } from '$lib/admin-config';

	/** 流量带 TrafficRibbon:贯穿产品的水平时间序列条带(signature 组件)。
	 * 数据源为现有 analyticsBuckets(bucket_start + tokens);后端按桶聚合返回,
	 * 每桶按成功(flow)/失败(danger)堆叠着色。API 返回降序,组件内翻转为时间正序。 */
	let { rows, height = 96 }: { rows: AnalyticsBucketRow[]; height?: number } = $props();

	let hoverIndex = $state(-1);

	const timeline = $derived([...rows].reverse());
	const maxTokens = $derived(Math.max(1, ...timeline.map((row) => Number(row.total_tokens ?? 0))));

	function barStyle(row: AnalyticsBucketRow) {
		const total = Math.max(0, Number(row.total_tokens ?? 0));
		const heightPct = Math.min(100, Math.max(2, (total / maxTokens) * 100));
		const failRatio = total > 0 ? Math.min(1, Number(row.failure_count ?? 0) / Math.max(1, Number(row.request_count ?? 0))) : 0;
		return { heightPct, failPct: failRatio * 100 };
	}

	function timeLabel(value: string): string {
		const date = parseServerUtcIso(value);
		return date.toLocaleString(undefined, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
	}
</script>

<div class="ribbon-chart" style={`height: ${height}px;`} role="img" aria-label={`流量带,共 ${timeline.length} 个时间桶`}>
	{#each timeline as row, index (row.bucket_start)}
		{@const style = barStyle(row)}
		<div
			class="ribbon-bar"
			style={`height: ${style.heightPct}%;`}
			onmouseenter={() => (hoverIndex = index)}
			onmouseleave={() => (hoverIndex = -1)}
		>
			<i style={`height: ${100 - style.failPct}%;`}></i>
			{#if style.failPct > 0}<i class="fail" style={`height: ${style.failPct}%;`}></i>{/if}
			{#if hoverIndex === index}
				<div class="ribbon-tip">
					{timeLabel(row.bucket_start)} · {fmtNumber(row.total_tokens)} tokens<br />
					{fmtNumber(row.request_count)} 请求 · 失败 {fmtNumber(row.failure_count)}
				</div>
			{/if}
		</div>
	{:else}
		<div class="empty" style="flex:1;">暂无时间桶数据。</div>
	{/each}
</div>
{#if timeline.length > 0}
	<div class="ribbon-axis">
		<span>{timeLabel(timeline[0].bucket_start)}</span>
		{#if timeline.length > 2}<span>{timeLabel(timeline[Math.floor(timeline.length / 2)].bucket_start)}</span>{/if}
		<span>{timeLabel(timeline[timeline.length - 1].bucket_start)}</span>
	</div>
{/if}
