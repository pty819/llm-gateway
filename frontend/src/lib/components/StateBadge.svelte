<script lang="ts">
	let { value, tone = 'neutral' }: { value: string | boolean | number | null | undefined; tone?: string } = $props();

	const label = $derived(translateValue(value));
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

	function translateValue(value: string | boolean | number | null | undefined): string | number {
		if (value === true) return '是';
		if (value === false) return '否';
		if (value === null || value === undefined || value === '') return '缺失';
		if (typeof value !== 'string') return value;
		const labels: Record<string, string> = {
			active: '启用',
			disabled: '禁用',
			success: '成功',
			failure: '失败',
			ready: '就绪',
			not_ready: '未就绪',
			all_pass: '全部放行',
			allowlist: '白名单'
		};
		return labels[value] ?? value;
	}
</script>

<span class="badge {computedTone}">{label}</span>
