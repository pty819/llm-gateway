<script lang="ts">
	import { Copy } from 'lucide-svelte';

	let {
		label,
		value,
		itemKey,
		copiedItem,
		onCopy
	}: {
		label: string;
		value: string;
		itemKey: string;
		copiedItem: string;
		onCopy: (value: string, key: string) => void;
	} = $props();
</script>

<div class="copy-value">
	<span>{label}</span>
	<code>{value}</code>
	<button class="secondary icon-button" type="button" onclick={() => onCopy(value, itemKey)} aria-label={`复制 ${label}`}>
		<Copy size={14} />
		{copiedItem === itemKey ? '已复制' : '复制'}
	</button>
</div>
