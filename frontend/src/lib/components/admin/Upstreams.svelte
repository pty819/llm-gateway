<script lang="ts">
	import type { Inventory, ResourceState, UpstreamHealth } from '$lib/api/types';
	import PageTitle from '$lib/components/PageTitle.svelte';
	import UpstreamTable from '$lib/components/UpstreamTable.svelte';

	type UpstreamForm = {
		model_alias_id: string;
		name: string;
		base_url: string;
		metrics_url: string;
		api_key_ref: string;
		api_key_value: string;
		health_path: string;
		extra_headers: string;
	};

	let {
		upstreams,
		models,
		healthResults,
		modelLabel,
		upstreamForm = $bindable(),
		onCreate,
		onCheck,
		onSetState,
		onPatch,
		onDelete,
		onError
	}: {
		upstreams: Inventory['upstreams'];
		models: Inventory['models'];
		healthResults: Record<string, UpstreamHealth | string>;
		modelLabel: (id: string | null | undefined) => string;
		upstreamForm: UpstreamForm;
		onCreate: () => void;
		onCheck: (id: string) => void;
		onSetState: (id: string, state: ResourceState) => void;
		onPatch: (id: string, patch: Record<string, unknown>) => void;
		onDelete: (upstream: Inventory['upstreams'][number]) => void;
		onError: (message: string) => void;
	} = $props();
</script>

<PageTitle title={'上游端点'} subtitle={'模型别名背后的同构 OpenAI 兼容副本池。'} />
<section class="panel">
	<h2>创建上游</h2>
	<div class="form-grid">
		<label>模型<select bind:value={upstreamForm.model_alias_id}><option value="">选择模型</option>{#each models as model}<option value={model.id}>{model.alias}</option>{/each}</select></label>
		<label>名称<input bind:value={upstreamForm.name} /></label>
		<label>Base URL<input bind:value={upstreamForm.base_url} placeholder="http://host:8000/v1" /></label>
		<label>Metrics URL<input bind:value={upstreamForm.metrics_url} placeholder="可选，例如 http://router-host:29000/metrics" /></label>
		<label>健康检查路径<input bind:value={upstreamForm.health_path} /></label>
		<label>API key 引用<input bind:value={upstreamForm.api_key_ref} /></label>
		<label>API key 明文<input type="password" bind:value={upstreamForm.api_key_value} /></label>
		<label>额外请求头<textarea bind:value={upstreamForm.extra_headers}></textarea></label>
		<button type="button" onclick={onCreate}>创建上游</button>
	</div>
</section>
<UpstreamTable rows={upstreams} {healthResults} {modelLabel} onCheck={onCheck} onState={onSetState} onPatch={onPatch} onDelete={onDelete} onError={onError} />
