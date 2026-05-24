<script lang="ts">
	let { command }: { command: string } = $props();
	let copied = $state(false);

	async function copy() {
		await navigator.clipboard.writeText(command);
		copied = true;
		setTimeout(() => (copied = false), 1400);
	}
</script>

<div class="command-block">
	<pre>{command}</pre>
	<button class="icon-button" type="button" onclick={copy}>{copied ? 'Copied' : 'Copy'}</button>
</div>
