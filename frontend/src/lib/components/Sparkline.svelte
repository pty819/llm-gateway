<script lang="ts">
	/** 迷你趋势线(KPI 卡背景趋势,高 28px)。纯 SVG,无第三方库(设计稿 P4)。 */
	let {
		values,
		width = 96,
		height = 28
	}: {
		values: number[];
		width?: number;
		height?: number;
	} = $props();

	const points = $derived.by(() => {
		if (values.length < 2) return '';
		const max = Math.max(...values, 1);
		const min = Math.min(...values, 0);
		const span = max - min || 1;
		const step = width / (values.length - 1);
		return values
			.map((value, index) => {
				const x = index * step;
				const y = height - 2 - ((value - min) / span) * (height - 4);
				return `${x.toFixed(1)},${y.toFixed(1)}`;
			})
			.join(' ');
	});
</script>

<svg class="sparkline" {width} {height} viewBox="0 0 {width} {height}" aria-hidden="true">
	{#if points}
		<polyline points={points} fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" />
	{/if}
</svg>
