<script lang="ts">
	import { MoreHorizontal } from 'lucide-svelte';

	/** 表格行 ⋯ 菜单:行内只保留高频开关,其余动作收纳于此(设计稿 L2)。 */
	type RowMenuItem = {
		label: string;
		onclick: () => void;
		danger?: boolean;
	};

	let { items, label = '操作' }: { items: RowMenuItem[]; label?: string } = $props();

	let open = $state(false);
	let wrapEl: HTMLDivElement | undefined = $state();

	$effect(() => {
		if (!open || typeof window === 'undefined') return;
		const onDocClick = (event: MouseEvent) => {
			if (wrapEl && !wrapEl.contains(event.target as Node)) open = false;
		};
		const onKey = (event: KeyboardEvent) => {
			if (event.key === 'Escape') open = false;
		};
		// 延迟绑定,避免本次点击立即触发关闭
		const timer = setTimeout(() => {
			window.addEventListener('click', onDocClick);
			window.addEventListener('keydown', onKey);
		}, 0);
		return () => {
			clearTimeout(timer);
			window.removeEventListener('click', onDocClick);
			window.removeEventListener('keydown', onKey);
		};
	});

	function handleKeydown(event: KeyboardEvent) {
		// 方向键在菜单项间移动(设计稿可访问性底线)
		if (!open || !wrapEl) return;
		if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
		event.preventDefault();
		const buttons = Array.from(wrapEl.querySelectorAll<HTMLButtonElement>('.row-menu button'));
		if (buttons.length === 0) return;
		const index = buttons.findIndex((button) => button === document.activeElement);
		const next =
			event.key === 'ArrowDown'
				? buttons[(index + 1) % buttons.length]
				: buttons[(index - 1 + buttons.length) % buttons.length];
		next?.focus();
	}
</script>

<div class="row-menu-wrap" bind:this={wrapEl} onkeydown={handleKeydown}>
	<button
		class="ghost icon-button sm"
		type="button"
		aria-haspopup="menu"
		aria-expanded={open}
		aria-label={label}
		onclick={(event) => {
			event.stopPropagation();
			open = !open;
		}}
	>
		<MoreHorizontal size={16} />
	</button>
	{#if open}
		<div class="row-menu" role="menu">
			{#each items as item}
				<button
					role="menuitem"
					type="button"
					class:danger={item.danger}
					onclick={(event) => {
						event.stopPropagation();
						open = false;
						item.onclick();
					}}
				>
					{item.label}
				</button>
			{/each}
		</div>
	{/if}
</div>
