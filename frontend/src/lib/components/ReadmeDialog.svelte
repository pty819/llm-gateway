<script lang="ts">
	import { marked } from 'marked';
	import DOMPurify from 'dompurify';

	let {
		readme,
		title,
		onClose
	}: {
		readme: string;
		title?: string;
		onClose: () => void;
	} = $props();

	let html = $derived(
		DOMPurify.sanitize(marked.parse(readme, { async: false }) as string)
	);

	function onBackdrop(event: MouseEvent) {
		if (event.target === event.currentTarget) onClose();
	}
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div class="modal-backdrop" role="presentation" onclick={onBackdrop}>
	<section class="modal readme-modal" aria-label="README">
		<header class="readme-head">
			<h2>{title ?? 'README'}</h2>
			<button class="secondary" type="button" onclick={onClose}>关闭</button>
		</header>
		<div class="readme-body">
			{@html html}
		</div>
	</section>
</div>

<style>
	.readme-modal {
		width: min(820px, 100%);
		max-height: min(80vh, 760px);
		display: flex;
		flex-direction: column;
	}

	.readme-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
	}

	.readme-body {
		overflow: auto;
		line-height: 1.6;
		font-size: 0.92rem;
	}

	.readme-body :global(h1),
	.readme-body :global(h2),
	.readme-body :global(h3) {
		margin: 1rem 0 0.5rem;
	}

	.readme-body :global(pre) {
		background: #f5f6f8;
		padding: 0.6rem 0.75rem;
		border-radius: 6px;
		overflow: auto;
	}

	.readme-body :global(code) {
		background: #f5f6f8;
		padding: 0.1rem 0.3rem;
		border-radius: 4px;
	}

	.readme-body :global(pre code) {
		background: none;
		padding: 0;
	}

	.readme-body :global(table) {
		border-collapse: collapse;
		width: 100%;
	}

	.readme-body :global(th),
	.readme-body :global(td) {
		border: 1px solid #e0e3e8;
		padding: 0.3rem 0.5rem;
		text-align: left;
	}
</style>
