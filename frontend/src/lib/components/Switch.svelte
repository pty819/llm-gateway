<script lang="ts">
	/** 标准开关控件,替代"禁用/启用"文字按钮(设计稿 P2 行内高频开关)。 */
	let {
		checked,
		onToggle,
		disabled = false,
		label
	}: {
		checked: boolean;
		onToggle: () => void;
		disabled?: boolean;
		label: string;
	} = $props();
</script>

<button
	class="switch"
	type="button"
	role="switch"
	aria-checked={checked}
	aria-label={label}
	title={label}
	{disabled}
	onclick={(event) => {
		event.stopPropagation();
		onToggle();
	}}
></button>
