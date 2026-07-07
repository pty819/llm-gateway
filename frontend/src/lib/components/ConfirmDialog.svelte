<script lang="ts">
	import Modal from '$lib/components/Modal.svelte';

	/**
	 * Drop-in replacement for `window.confirm` that is testable by Playwright
	 * and accessible (Modal provides role=dialog/aria-modal, Escape + backdrop
	 * close, autofocus). Driven via the bindable `message`: set it to a
	 * non-null string to open, and it resets to null when the user confirms
	 * or cancels (Escape/backdrop count as cancel).
	 *
	 * For multi-step confirm flows the promise-based pattern in `+page.svelte`
	 * (`askConfirm`) is the recommended alternative.
	 */
	let {
		message = $bindable(null as string | null),
		title = '请确认',
		confirmLabel = '确认',
		cancelLabel = '取消',
		danger = true,
		busy = false,
		onConfirm,
		onCancel
	}: {
		message?: string | null;
		title?: string;
		confirmLabel?: string;
		cancelLabel?: string;
		danger?: boolean;
		busy?: boolean;
		onConfirm?: () => void;
		onCancel?: () => void;
	} = $props();

	let open = $derived(message !== null);

	function confirm() {
		if (busy) return;
		message = null;
		onConfirm?.();
	}

	function cancel() {
		message = null;
		onCancel?.();
	}
</script>

<Modal {open} onClose={cancel} ariaLabel={title} width="narrow">
	<header>
		<h2>{title}</h2>
	</header>
	<p>{message}</p>
	<footer class="actions">
		<button
			class={danger ? 'danger' : ''}
			type="button"
			onclick={confirm}
			disabled={busy}
		>
			{busy ? '处理中' : confirmLabel}
		</button>
		<button class="secondary" type="button" onclick={cancel} disabled={busy}>
			{cancelLabel}
		</button>
	</footer>
</Modal>
