<script lang="ts">
	import type { Inventory } from '$lib/api/types';
	import { msLabel, ratioLabel } from '$lib/admin-config';

	let {
		rows,
		maxTokens
	}: {
		rows: Inventory['analyticsBuckets'];
		maxTokens: number;
	} = $props();
</script>

<div class="table-wrap">
	<table>
		<thead><tr><th>时间</th><th>压力</th><th>请求</th><th>Token</th><th>缓存</th><th>成功 / 失败</th><th>延迟</th><th>TTFT</th><th>流时长</th><th>vLLM</th></tr></thead>
		<tbody>
			{#each rows as row}
				<tr>
					<td>{new Date(row.bucket_start).toLocaleString()}</td>
					<td><div class="bar-track"><span style={`width: ${Math.max(4, Math.round((row.total_tokens / maxTokens) * 100))}%`}></span></div></td>
					<td>{row.request_count}</td>
					<td>{row.total_tokens}<br /><span class="muted">入 {row.prompt_tokens} / 出 {row.completion_tokens}</span></td>
					<td>{row.cached_tokens}</td>
					<td>{row.success_count} / {row.failure_count}</td>
					<td>{msLabel(row.avg_latency_ms)}</td>
					<td>{msLabel(row.avg_ttft_ms)}</td>
					<td>{msLabel(row.avg_stream_duration_ms)}</td>
					<td>{row.vllm_metrics_count ? `${row.vllm_metrics_count} 条` : '无上游指标'}<br /><span class="muted">queue {msLabel(row.avg_queue_ms)} · prefill {msLabel(row.avg_prefill_ms)} · decode {msLabel(row.avg_decode_ms)} · KV {ratioLabel(row.avg_kv_cache_usage)}</span></td>
				</tr>
			{:else}
				<tr><td colspan="10" class="empty">这个时间范围内暂无趋势数据。</td></tr>
			{/each}
		</tbody>
	</table>
</div>
