<script lang="ts">
	import { maskSecrets } from '$lib/validators';

	let { value }: { value: unknown } = $props();
	const displayValue = $derived(JSON.stringify(maskSecrets(value), null, 2));
</script>

<pre class="json-viewer">{displayValue}</pre>
