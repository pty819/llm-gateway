<script lang="ts">
	import type { Inventory } from '$lib/api/types';

	let {
		rows,
		subjectLabel,
		projectLabel
	}: {
		rows: Inventory['usage'];
		subjectLabel: (id: string | null | undefined) => string;
		projectLabel: (id: string | null | undefined) => string;
	} = $props();
</script>

<div class="table-wrap">
	<table>
		<thead><tr><th>模型</th><th>用户</th><th>项目</th><th>请求数</th><th>输入</th><th>输出</th><th>总计</th><th>成功</th><th>失败</th></tr></thead>
		<tbody>
			{#each rows as row}
				<tr><td>{row.model_alias ?? '无'}</td><td>{subjectLabel(row.subject_id)}</td><td>{projectLabel(row.project_id)}</td><td>{row.request_count}</td><td>{row.prompt_tokens}</td><td>{row.completion_tokens}</td><td>{row.total_tokens}</td><td>{row.success_count}</td><td>{row.failure_count}</td></tr>
			{:else}
				<tr><td colspan="9" class="empty">这个时间范围内暂无用量数据。</td></tr>
			{/each}
		</tbody>
	</table>
</div>
