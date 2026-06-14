<script lang="ts">
	let { title, subtitle }: { title: string; subtitle: string } = $props();
</script>

<div class="page-header">
	<div>
		<h1>{title}</h1>
		<p>{subtitle}</p>
	</div>
</div>
