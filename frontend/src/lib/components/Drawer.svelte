<script lang="ts">
	import type { Snippet } from 'svelte';
	import { X } from 'lucide-svelte';

	let {
		open,
		title,
		subtitle = '',
		wide = false,
		onClose,
		children,
		footer
	}: {
		open: boolean;
		title: string;
		subtitle?: string;
		wide?: boolean;
		onClose: () => void;
		children: Snippet;
		footer?: Snippet;
	} = $props();

	let bodyEl: HTMLDivElement | undefined = $state();

	// Esc 关闭;打开时聚焦抽屉主体,保证键盘可达
	$effect(() => {
		if (!open || typeof window === 'undefined') return;
		const onKey = (event: KeyboardEvent) => {
			if (event.key === 'Escape') onClose();
		};
		window.addEventListener('keydown', onKey);
		bodyEl?.focus();
		return () => window.removeEventListener('keydown', onKey);
	});
</script>

{#if open}
	<div class="drawer-backdrop" onclick={onClose} role="presentation"></div>
	<aside class="drawer" class:wide role="dialog" aria-modal="true" aria-label={title}>
		<header class="drawer-head">
			<div>
				<h2>{title}</h2>
				{#if subtitle}<p>{subtitle}</p>{/if}
			</div>
			<button class="ghost icon-button" type="button" onclick={onClose} aria-label="关闭">
				<X size={16} />
			</button>
		</header>
		<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
		<div class="drawer-body" bind:this={bodyEl} tabindex="-1">
			{@render children()}
		</div>
		{#if footer}
			<footer class="drawer-footer">
				{@render footer()}
			</footer>
		{/if}
	</aside>
{/if}
