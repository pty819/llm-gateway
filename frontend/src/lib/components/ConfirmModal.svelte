<script lang="ts">
	import { AlertTriangle } from 'lucide-svelte';

	/** 破坏性操作二次确认弹层,替代 window.confirm(设计稿 I3/L3)。 */
	let {
		open,
		title,
		message,
		confirmLabel = '确认',
		danger = true,
		loading = false,
		onConfirm,
		onCancel
	}: {
		open: boolean;
		title: string;
		message: string;
		confirmLabel?: string;
		danger?: boolean;
		loading?: boolean;
		onConfirm: () => void;
		onCancel: () => void;
	} = $props();

	$effect(() => {
		if (!open || typeof window === 'undefined') return;
		const onKey = (event: KeyboardEvent) => {
			if (event.key === 'Escape') onCancel();
		};
		window.addEventListener('keydown', onKey);
		return () => window.removeEventListener('keydown', onKey);
	});
</script>

{#if open}
	<div class="modal-backdrop" role="presentation" onclick={onCancel}>
		<section class="modal" style="width: min(440px, 100%);" aria-label={title} onclick={(e) => e.stopPropagation()}>
			<header style="display:flex; gap:12px; align-items:flex-start;">
				{#if danger}
					<span style="color: var(--danger); margin-top:2px;"><AlertTriangle size={20} /></span>
				{/if}
				<div>
					<h2>{title}</h2>
					<p style="margin-top:6px; white-space: pre-wrap;">{message}</p>
				</div>
			</header>
			<footer>
				<button class="secondary" type="button" onclick={onCancel}>取消</button>
				<button class={danger ? 'danger' : ''} type="button" disabled={loading} onclick={onConfirm}>
					{#if loading}<span class="spinner"></span>{/if}
					{confirmLabel}
				</button>
			</footer>
		</section>
	</div>
{/if}
