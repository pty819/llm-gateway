<script lang="ts">
	import type { Inventory } from '$lib/api/types';
	import { msLabel, short } from '$lib/admin-config';

	let { rows }: { rows: Inventory['analyticsDrilldown'] } = $props();
</script>

<div class="table-wrap">
	<table>
		<thead><tr><th>维度</th><th>请求</th><th>Token</th><th>缓存</th><th>成功 / 失败</th><th>延迟</th><th>TTFT</th><th>Retry / Fallback</th><th>vLLM</th></tr></thead>
		<tbody>
			{#each rows as row}
				<tr>
					<td><strong>{row.dimension_label}</strong><br /><span class="muted">{short(row.dimension_id)}</span></td>
					<td>{row.request_count}</td>
					<td>{row.total_tokens}<br /><span class="muted">入 {row.prompt_tokens} / 出 {row.completion_tokens}</span></td>
					<td>{row.cached_tokens}</td>
					<td>{row.success_count} / {row.failure_count}</td>
					<td>{msLabel(row.avg_latency_ms)}</td>
					<td>{msLabel(row.avg_ttft_ms)}</td>
					<td>{row.retry_count} / {row.fallback_count}<br /><span class="muted">fallback token {row.fallback_tokens}</span></td>
					<td>{row.vllm_metrics_count ? `${row.vllm_metrics_count} 条` : '无上游指标'}</td>
				</tr>
			{:else}
				<tr><td colspan="9" class="empty">暂无 drilldown 数据。</td></tr>
			{/each}
		</tbody>
	</table>
</div>
