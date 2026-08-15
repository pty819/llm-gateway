<script lang="ts">
	import type { AuditEvent } from '$lib/api/types';
	import StateBadge from '$lib/components/StateBadge.svelte';
	import { parseServerUtcIso, short } from '$lib/admin-config';

	let {
		rows,
		onDetail
	}: {
		rows: AuditEvent[];
		onDetail: (event: AuditEvent) => void;
	} = $props();
</script>

<div class="table-wrap">
	<table>
		<thead><tr><th>时间</th><th>动作</th><th>资源</th><th>结果</th><th>详情</th></tr></thead>
		<tbody>
			{#each rows as event}
				<tr>
					<td>{parseServerUtcIso(event.created_at).toLocaleString()}</td>
					<td>{event.action}</td>
					<td>{event.resource_type}<br /><span class="muted">{short(event.resource_id)}</span></td>
					<td><StateBadge value={event.outcome} /></td>
					<td><button class="secondary icon-button" type="button" onclick={() => onDetail(event)}>查看</button></td>
				</tr>
			{:else}
				<tr><td colspan="5" class="empty">暂无审计事件。</td></tr>
			{/each}
		</tbody>
	</table>
</div>
