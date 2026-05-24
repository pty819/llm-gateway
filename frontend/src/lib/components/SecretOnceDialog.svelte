<script lang="ts">
	let {
		secret,
		onClose
	}: {
		secret: string;
		onClose: () => void;
	} = $props();
	let copied = $state(false);

	async function copy() {
		await navigator.clipboard.writeText(secret);
		copied = true;
	}
</script>

{#if secret}
	<div class="modal-backdrop" role="presentation">
		<section class="modal" aria-label="One-time gateway key">
			<header>
				<h2>Gateway key issued</h2>
				<p>This plaintext key is only available now.</p>
			</header>
			<pre class="secret">{secret}</pre>
			<footer>
				<button type="button" onclick={copy}>{copied ? 'Copied' : 'Copy key'}</button>
				<button class="secondary" type="button" onclick={onClose}>Close and hide</button>
			</footer>
		</section>
	</div>
{/if}
