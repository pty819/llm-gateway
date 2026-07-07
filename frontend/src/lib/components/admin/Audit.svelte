<script lang="ts">
	import type { AuditEvent, Inventory } from '$lib/api/types';
	import { PAGE_SIZE } from '$lib/admin-config';
	import PageTitle from '$lib/components/PageTitle.svelte';
	import Pagination from '$lib/components/Pagination.svelte';
	import AuditTable from '$lib/components/AuditTable.svelte';

	let {
		auditRows,
		auditPageRows,
		auditPage = $bindable(),
		onDetail
	}: {
		auditRows: AuditEvent[] | Inventory['audit'];
		auditPageRows: AuditEvent[] | Inventory['audit'];
		auditPage: number;
		onDetail: (event: AuditEvent) => void;
	} = $props();
</script>

<PageTitle title={'审计'} subtitle={'最近的权限变更和安全相关事件。'} />
<section class="panel"><AuditTable rows={auditPageRows} onDetail={onDetail} /><Pagination total={auditRows.length} page={auditPage} size={PAGE_SIZE.audit} onPage={(page) => (auditPage = page)} /></section>
