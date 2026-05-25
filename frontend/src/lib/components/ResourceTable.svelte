<script lang="ts" generics="T extends Record<string, unknown>">
	type Column<T> = {
		key: string;
		label: string;
		value: (row: T) => unknown;
	};

	let {
		rows,
		columns,
		empty = '暂无数据',
		onSelect
	}: {
		rows: T[];
		columns: Column<T>[];
		empty?: string;
		onSelect?: (row: T) => void;
	} = $props();
</script>

<div class="table-wrap">
	<table>
		<thead>
			<tr>
				{#each columns as column}
					<th>{column.label}</th>
				{/each}
			</tr>
		</thead>
		<tbody>
			{#if rows.length === 0}
				<tr>
					<td colspan={columns.length} class="empty">{empty}</td>
				</tr>
			{:else}
				{#each rows as row}
					<tr class:clickable={Boolean(onSelect)} onclick={() => onSelect?.(row)}>
						{#each columns as column}
							<td>{column.value(row)}</td>
						{/each}
					</tr>
				{/each}
			{/if}
		</tbody>
	</table>
</div>
