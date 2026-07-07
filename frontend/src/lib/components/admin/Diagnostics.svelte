<script lang="ts">
	import type { Diagnostics, HealthCheckConfig, Inventory, ReadyStatus, ResourceState, UpstreamHealth } from '$lib/api/types';
	import PageTitle from '$lib/components/PageTitle.svelte';
	import UpstreamTable from '$lib/components/UpstreamTable.svelte';

	let {
		ready,
		diagnostics,
		upstreams,
		healthResults,
		healthCheckConfig,
		healthCheckToggling,
		modelLabel,
		onToggleHealthCheck,
		onCheck,
		onSetState,
		onPatch,
		onDelete,
		onError
	}: {
		ready: ReadyStatus | null;
		diagnostics: Diagnostics | null;
		upstreams: Inventory['upstreams'];
		healthResults: Record<string, UpstreamHealth | string>;
		healthCheckConfig: HealthCheckConfig | null;
		healthCheckToggling: boolean;
		modelLabel: (id: string | null | undefined) => string;
		onToggleHealthCheck: () => void;
		onCheck: (id: string) => void;
		onSetState: (id: string, state: ResourceState) => void;
		onPatch: (id: string, patch: Record<string, unknown>) => void;
		onDelete: (upstream: Inventory['upstreams'][number]) => void;
		onError: (message: string) => void;
	} = $props();
</script>

<PageTitle title={'诊断'} subtitle={'运行时依赖和上游健康检查。'} />
<div class="grid"><div class="metric"><span>Postgres</span><strong>{ready?.checks.postgres ? '正常' : '异常'}</strong></div><div class="metric"><span>Redis</span><strong>{ready?.checks.redis ? '正常' : '异常'}</strong></div><div class="metric"><span>环境</span><strong>{diagnostics?.environment}</strong></div></div>
<section class="panel">
	<h2>健康巡检</h2>
	<p class="muted">自动探测每个活跃上游的 <code>/models</code>，故障时在 Redis 标记 UNHEALTHY 并从路由排除。关闭后 sidecar 仍运行但跳过探测，已有标记靠 TTL 自动过期恢复。</p>
	<div style="display:flex;align-items:center;gap:0.75rem;flex-wrap:wrap">
		<strong>自动巡检：{healthCheckConfig?.enabled ? '已开启' : '已关闭'}</strong>
		{#if healthCheckConfig}
			<span class="muted">来源：{healthCheckConfig.source === 'redis_override' ? '运行时覆盖' : '环境变量默认'}</span>
			<button class="secondary" type="button" disabled={healthCheckToggling} onclick={onToggleHealthCheck}>{healthCheckConfig.enabled ? '关闭巡检' : '开启巡检'}</button>
		{/if}
	</div>
</section>
<UpstreamTable rows={upstreams} {healthResults} {modelLabel} onCheck={onCheck} onState={onSetState} onPatch={onPatch} onDelete={onDelete} onError={onError} />
