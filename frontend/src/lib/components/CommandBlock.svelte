<script lang="ts">
	let { command }: { command: string } = $props();
	let copied = $state(false);

	async function copy() {
		if (navigator.clipboard?.writeText) {
			await navigator.clipboard.writeText(command);
		} else {
			const textarea = document.createElement('textarea');
			textarea.value = command;
			textarea.style.position = 'fixed';
			textarea.style.opacity = '0';
			document.body.appendChild(textarea);
			textarea.focus();
			textarea.select();
			document.execCommand('copy');
			textarea.remove();
		}
		copied = true;
		setTimeout(() => (copied = false), 1400);
	}
</script>

<div class="command-block">
	<pre>{command}</pre>
	<button class="icon-button" type="button" onclick={copy}>{copied ? '已复制' : '复制'}</button>
</div>
