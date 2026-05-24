<script lang="ts">
	let { value, tone = 'neutral' }: { value: string | boolean | number | null | undefined; tone?: string } = $props();

	const label = $derived(value === true ? 'yes' : value === false ? 'no' : (value ?? 'missing'));
	const computedTone = $derived(
		tone === 'neutral' && typeof value === 'string'
			? value === 'active' || value === 'success'
				? 'success'
				: value === 'disabled' || value === 'failure'
					? 'danger'
					: value.includes('failure') || value.includes('denied') || value.includes('limited')
						? 'danger'
						: 'neutral'
			: tone
	);
</script>

<span class="badge {computedTone}">{label}</span>
