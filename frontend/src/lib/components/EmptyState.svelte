<script lang="ts">
	import { Inbox } from 'lucide-svelte';
	import type { ComponentType } from 'svelte';

	/** 空状态:居中图形 + 指引 + 行动按钮(设计稿 I2)。 */
	let {
		icon = Inbox,
		title,
		hint = '',
		actionLabel = '',
		onAction
	}: {
		icon?: ComponentType;
		title: string;
		hint?: string;
		actionLabel?: string;
		onAction?: () => void;
	} = $props();

	const Icon = $derived(icon);
</script>

<div class="empty-state" role="status">
	<Icon size={40} strokeWidth={1.2} />
	<strong>{title}</strong>
	{#if hint}<p>{hint}</p>{/if}
	{#if actionLabel && onAction}
		<button type="button" onclick={onAction}>{actionLabel}</button>
	{/if}
</div>
