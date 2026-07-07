<script lang="ts" module>
	export type ModalWidth = 'narrow' | 'default' | 'wide';
</script>

<script lang="ts">
	import { tick } from 'svelte';

	/**
	 * Reusable modal dialog.
	 *
	 * Replaces ad-hoc `modal-backdrop` blocks scattered across the app
	 * (`+page.svelte`, the market dialogs, etc.) which used
	 * `role="presentation"` with no Escape handling, no `aria-modal`, and no
	 * focus management. This component provides:
	 *   - `role="dialog" aria-modal="true"`
	 *   - Escape closes
	 *   - backdrop click closes (only when the backdrop itself is clicked)
	 *   - autofocus on the panel when opened (focus-capable; full trap is
	 *     intentionally left to callers / future work)
	 *   - a `width` modifier for confirm (narrow) vs form (default/wide) use
	 *
	 * `open` is bindable so callers can both drive it (`bind:open`) and react
	 * via `onClose`.
	 */
	let {
		open = $bindable(false),
		onClose,
		ariaLabel,
		width = 'default',
		children
	}: {
		open?: boolean;
		onClose?: () => void;
		ariaLabel?: string;
		width?: ModalWidth;
		children?: import('svelte').Snippet;
	} = $props();

	let panel: HTMLElement | null = $state(null);

	function close() {
		open = false;
		onClose?.();
	}

	function onKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			e.preventDefault();
			close();
		}
	}

	function onBackdrop(event: MouseEvent) {
		// only close when the backdrop itself (not a child) is clicked
		if (event.target === event.currentTarget) close();
	}

	$effect(() => {
		if (open && panel) {
			void tick().then(() => panel?.focus());
		}
	});
</script>

<svelte:window onkeydown={open ? onKeydown : undefined} />

{#if open}
	<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
	<div class="modal-backdrop" role="presentation" onclick={onBackdrop}>
		<div
			bind:this={panel}
			class="modal"
			class:narrow={width === 'narrow'}
			class:wide={width === 'wide'}
			role="dialog"
			aria-modal="true"
			aria-label={ariaLabel}
			tabindex="-1"
		>
			{@render children?.()}
		</div>
	</div>
{/if}

<style>
	.modal {
		outline: none;
	}

	.modal.narrow {
		width: min(420px, 100%);
	}

	.modal.wide {
		width: min(820px, 100%);
	}
</style>
