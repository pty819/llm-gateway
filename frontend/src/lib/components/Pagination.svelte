<script lang="ts">
	import { ChevronLeft, ChevronRight } from 'lucide-svelte';
	import { pageCountTotal } from '$lib/admin-config';
	import { fmtNumber } from '$lib/format';

	/** 数字分页(设计稿 L8):‹ 1 2 … 345 346 › + 可选每页条数选择。
	 * 保持前端分页逻辑(pageRows)不变,仅升级呈现。 */
	let {
		total,
		page,
		size,
		onPage,
		sizes = [],
		onSizeChange
	}: {
		total: number;
		page: number;
		size: number;
		onPage: (page: number) => void;
		sizes?: number[];
		onSizeChange?: (size: number) => void;
	} = $props();

	const pages = $derived(pageCountTotal(total, size));
	const safePage = $derived(Math.min(page, pages));
	const rangeStart = $derived(total === 0 ? 0 : (safePage - 1) * size + 1);
	const rangeEnd = $derived(Math.min(safePage * size, total));

	/** 生成省略页码序列,如 [1, '…', 4, 5, 6, '…', 346]。 */
	const pageItems = $derived.by(() => {
		const items: Array<number | 'gap-left' | 'gap-right'> = [];
		const window = new Set<number>([1, 2, pages - 1, pages, safePage - 1, safePage, safePage + 1]);
		let previous = 0;
		for (let index = 1; index <= pages; index++) {
			if (!window.has(index)) continue;
			if (index - previous > 1) items.push(index - previous > 2 ? `gap-${items.length === 0 ? 'left' : 'right'}` as const : index - 1);
			items.push(index);
			previous = index;
		}
		return items;
	});
</script>

<div class="list-footer">
	<div class="actions" style="gap: var(--sp-3);">
		<span class="muted num">显示 {rangeStart}–{rangeEnd} / 共 {fmtNumber(total)}</span>
		{#if sizes.length > 0 && onSizeChange}
			<select class="size-select" aria-label="每页条数" value={size} onchange={(event) => onSizeChange(Number(event.currentTarget.value))}>
				{#each sizes as option}
					<option value={option}>{option} 条/页</option>
				{/each}
			</select>
		{/if}
	</div>
	<nav class="pager" aria-label="分页">
		<button type="button" disabled={safePage <= 1} onclick={() => onPage(safePage - 1)} aria-label="上一页">
			<ChevronLeft size={14} />
		</button>
		{#each pageItems as item}
			{#if typeof item === 'number'}
				<button type="button" class:current={item === safePage} aria-current={item === safePage ? 'page' : undefined} onclick={() => onPage(item)}>{item}</button>
			{:else}
				<span class="pager-gap">…</span>
			{/if}
		{/each}
		<button type="button" disabled={safePage >= pages} onclick={() => onPage(safePage + 1)} aria-label="下一页">
			<ChevronRight size={14} />
		</button>
	</nav>
</div>
