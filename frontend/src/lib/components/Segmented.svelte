<script lang="ts">
	/** Segmented control:时间范围等互斥快捷选项(设计稿 D2)。 */
	let {
		options,
		selected,
		onSelect
	}: {
		options: { value: string; label: string }[];
		selected: string;
		onSelect: (value: string) => void;
	} = $props();
</script>

<div class="segmented" role="tablist">
	{#each options as option}
		<button
			type="button"
			role="tab"
			aria-selected={option.value === selected}
			class:active={option.value === selected}
			onclick={() => onSelect(option.value)}
		>
			{option.label}
		</button>
	{/each}
</div>
