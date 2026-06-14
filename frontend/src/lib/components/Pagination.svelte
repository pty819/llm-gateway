<script lang="ts">
	import { pageCountTotal } from '$lib/admin-config';

	let {
		total,
		page,
		size,
		onPage
	}: {
		total: number;
		page: number;
		size: number;
		onPage: (page: number) => void;
	} = $props();

	const pages = $derived(pageCountTotal(total, size));
</script>

<div class="actions">
	<span class="muted">共 {total} 条 · 第 {Math.min(page, pages)} / {pages} 页</span>
	<button class="secondary" type="button" disabled={page <= 1} onclick={() => onPage(page - 1)}>上一页</button>
	<button class="secondary" type="button" disabled={page >= pages} onclick={() => onPage(page + 1)}>下一页</button>
</div>
