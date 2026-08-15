<script lang="ts">
	import { CheckCircle2, XCircle } from 'lucide-svelte';
	import { toasts, dismissToast } from '$lib/state/toast';
</script>

<div class="toast-stack" aria-live="polite">
	{#each $toasts as toast (toast.id)}
		<button
			type="button"
			class="toast {toast.kind === 'ok' ? 'ok' : 'err'}"
			style="width:100%; height:auto; justify-content:flex-start; text-align:left; border-radius: var(--radius-m);"
			onclick={() => dismissToast(toast.id)}
		>
			{#if toast.kind === 'ok'}<CheckCircle2 size={16} />{:else}<XCircle size={16} />{/if}
			<span>{toast.message}</span>
		</button>
	{/each}
</div>
