<script lang="ts">
	import type {
		Inventory,
		RuntimeMetricsSnapshot,
		RuntimeMetricsUpstream
	} from '$lib/api/types';
	import {
		PAGE_SIZE,
		short,
		msLabel,
		ratioLabel,
		tokenRateLabel,
		metricsKindLabel,
		subjectDisplay
	} from '$lib/admin-config';
	import StateBadge from '$lib/components/StateBadge.svelte';
	import PageTitle from '$lib/components/PageTitle.svelte';
	import Pagination from '$lib/components/Pagination.svelte';
	import AnalyticsBucketTable from '$lib/components/AnalyticsBucketTable.svelte';
	import AnalyticsDrilldownTable from '$lib/components/AnalyticsDrilldownTable.svelte';
	import UsageTable from '$lib/components/UsageTable.svelte';

	let {
		inventory,
		realtimeStatus,
		realtimeLocked = $bindable(),
		realtime,
		realtimeRows,
		realtimeUpdatedLabel,
		totals,
		analyticsPerformance,
		analyticsMaxTokens,
		visibleAnalyticsBuckets,
		visibleAnalyticsDrilldown,
		visibleUsageRows,
		usageStart = $bindable(),
		usageEnd = $bindable(),
		analyticsBucket = $bindable(),
		analyticsDimension = $bindable(),
		modelFilter = $bindable(),
		subjectFilter = $bindable(),
		projectFilter = $bindable(),
		usageSubjectSearch = $bindable(),
		subjectOptions,
		subjectLabel,
		projectLabel,
		setUsageRange,
		onRefreshUsageAnalytics,
		onStartRealtimeStream,
		loading
	}: {
		inventory: Inventory;
		realtimeStatus: string;
		realtimeLocked: boolean;
		realtime: RuntimeMetricsSnapshot | null;
		realtimeRows: RuntimeMetricsUpstream[];
		realtimeUpdatedLabel: string;
		totals: { requests: number; total: number; success: number; failure: number };
		analyticsPerformance: {
			latencyTotal: number;
			latencyWeight: number;
			ttftTotal: number;
			ttftWeight: number;
			retry: number;
			fallback: number;
			vllmObserved: number;
			requests: number;
		};
		analyticsMaxTokens: number;
		visibleAnalyticsBuckets: Inventory['analyticsBuckets'];
		visibleAnalyticsDrilldown: Inventory['analyticsDrilldown'];
		visibleUsageRows: Inventory['usage'];
		usageStart: string;
		usageEnd: string;
		analyticsBucket: 'minute' | 'hour' | 'day';
		analyticsDimension: 'model' | 'subject' | 'project' | 'endpoint' | 'outcome' | 'streaming';
		modelFilter: string;
		subjectFilter: string;
		projectFilter: string;
		usageSubjectSearch: string;
		subjectOptions: (query: string) => Inventory['subjects'];
		subjectLabel: (id: string | null | undefined) => string;
		projectLabel: (id: string | null | undefined) => string;
		setUsageRange: (days: number) => void;
		onRefreshUsageAnalytics: () => void;
		onStartRealtimeStream: () => void;
		loading: boolean;
	} = $props();
</script>

<PageTitle title={'用量总览'} subtitle={'默认查看最近一周的推理压力；需要更细或更长窗口时直接调整时间范围。'} />
<section class="panel">
	<h2>资源概览</h2>
	<div class="grid">
		<div class="metric"><span>用户</span><strong>{inventory.subjects.length}</strong></div>
		<div class="metric"><span>项目</span><strong>{inventory.projects.length}</strong></div>
		<div class="metric"><span>密钥</span><strong>{inventory.keys.length}</strong></div>
		<div class="metric"><span>模型</span><strong>{inventory.models.length}</strong></div>
	</div>
</section>
<section class="panel">
	<div class="section-head">
		<div>
			<h2>实时负载</h2>
			<p>每个上游副本的 vLLM 压力来自对应 <code>/metrics</code>；多个浏览器共享 Redis 缓存。</p>
		</div>
		<div class="actions">
			<StateBadge value={realtimeStatus} tone={realtimeStatus === '已连接' ? 'success' : 'neutral'} />
			<button class={realtimeLocked ? '' : 'secondary'} type="button" onclick={() => (realtimeLocked = !realtimeLocked)}>{realtimeLocked ? '解锁排序' : '锁定顺序·显示全部'}</button>
			<button class="secondary" type="button" onclick={onStartRealtimeStream}>重连</button>
		</div>
	</div>
	<div class="grid">
		<div class="metric"><span>vLLM 指标 token/s</span><strong>{realtime?.vllm.tokens_per_second == null ? '等待样本' : tokenRateLabel(realtime.vllm.tokens_per_second)}</strong></div>
		<div class="metric"><span>网关当前上游连接</span><strong>{realtime?.active_connections ?? 0}</strong></div>
		<div class="metric"><span>vLLM running / waiting</span><strong>{realtime?.vllm.running ?? '无'} / {realtime?.vllm.waiting ?? '无'}</strong></div>
		<div class="metric"><span>最高 KV cache</span><strong>{ratioLabel(realtime?.vllm.max_kv_cache_usage)}</strong></div>
		<div class="metric"><span>metrics 可用上游</span><strong>{realtime?.vllm.ok_upstreams ?? 0} / {realtime?.vllm.configured_upstreams ?? realtime?.vllm.observed_upstreams ?? 0}</strong></div>
		<div class="metric"><span>metrics 已忽略</span><strong>{realtime?.vllm.ignored_upstreams ?? 0}</strong></div>
		<div class="metric"><span>上游抓取缓存</span><strong>{realtime?.metrics_cache_seconds ?? realtime?.window_seconds ?? 3} 秒</strong></div>
		<div class="metric"><span>更新时间</span><strong>{realtimeUpdatedLabel}</strong></div>
	</div>
	<div class="table-wrap">
		<table>
			<thead><tr><th>上游</th><th>模型</th><th>类型</th><th>token/s</th><th>网关连接</th><th>vLLM running / waiting</th><th>Router 负载</th><th>KV / Prefix</th><th>metrics</th></tr></thead>
			<tbody>
				{#each realtimeRows as upstream (upstream.upstream_id)}
					<tr>
						<td>{upstream.upstream_name}<br /><span class="muted">{short(upstream.upstream_id)}</span></td>
						<td>{upstream.model_alias || '未知'}</td>
						<td>{metricsKindLabel(upstream.vllm?.kind)}</td>
						<td>{upstream.vllm?.tokens_per_second == null ? '等待样本' : tokenRateLabel(upstream.vllm.tokens_per_second)}</td>
						<td>{upstream.active_connections}</td>
						<td>{upstream.vllm?.running ?? '无'} / {upstream.vllm?.waiting ?? '无'}</td>
						<td>{upstream.vllm?.router?.running_requests ?? upstream.vllm?.router?.worker_load ?? '无'} / {upstream.vllm?.router?.active_workers ?? '无'}</td>
						<td>{ratioLabel(upstream.vllm?.kv_cache_usage)} / {ratioLabel(upstream.vllm?.prefix_cache_hit_ratio)}</td>
						<td>{upstream.vllm?.ok ? '正常' : upstream.vllm?.error ?? '未抓取'}<br /><span class="muted">{upstream.vllm?.metrics_url ?? ''}</span></td>
					</tr>
				{:else}
					<tr><td colspan="9" class="empty">暂无实时负载数据。</td></tr>
				{/each}
			</tbody>
		</table>
	</div>
</section>
<section class="panel"><div class="actions"><button class="secondary" type="button" onclick={() => setUsageRange(1 / 24)}>最近 1 小时</button><button class="secondary" type="button" onclick={() => setUsageRange(1)}>最近 1 天</button><button class="secondary" type="button" onclick={() => setUsageRange(7)}>最近 1 周</button><button class="secondary" type="button" onclick={() => setUsageRange(30)}>最近 1 月</button></div><div class="form-grid"><label>开始时间<input type="datetime-local" bind:value={usageStart} /></label><label>结束时间<input type="datetime-local" bind:value={usageEnd} /></label><label>时间粒度<select bind:value={analyticsBucket}><option value="minute">分钟</option><option value="hour">小时</option><option value="day">天</option></select></label><label>分析维度<select bind:value={analyticsDimension}><option value="model">模型</option><option value="subject">用户</option><option value="project">项目</option><option value="endpoint">协议</option><option value="outcome">结果</option><option value="streaming">流式</option></select></label><label>模型筛选<select bind:value={modelFilter}><option value="">全部</option>{#each inventory.models as model}<option value={model.alias}>{model.alias}</option>{/each}</select></label><label>搜索用户<input bind:value={usageSubjectSearch} placeholder="输入姓名或工号" /></label><label>用户筛选<select bind:value={subjectFilter}><option value="">全部</option>{#each subjectOptions(usageSubjectSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label><label>项目筛选<select bind:value={projectFilter}><option value="">全部</option>{#each inventory.projects as project}<option value={project.id}>{project.name}</option>{/each}</select></label><button type="button" onclick={onRefreshUsageAnalytics} disabled={loading}>{loading ? '查询中' : '查询'}</button></div></section>
<div class="grid"><div class="metric"><span>请求数</span><strong>{totals.requests}</strong></div><div class="metric"><span>总 token</span><strong>{totals.total}</strong></div><div class="metric"><span>成功</span><strong>{totals.success}</strong></div><div class="metric"><span>失败</span><strong>{totals.failure}</strong></div><div class="metric"><span>平均延迟</span><strong>{msLabel(analyticsPerformance.latencyWeight ? analyticsPerformance.latencyTotal / analyticsPerformance.latencyWeight : null)}</strong></div><div class="metric"><span>平均 TTFT</span><strong>{msLabel(analyticsPerformance.ttftWeight ? analyticsPerformance.ttftTotal / analyticsPerformance.ttftWeight : null)}</strong></div><div class="metric"><span>Retry / Fallback</span><strong>{analyticsPerformance.retry} / {analyticsPerformance.fallback}</strong></div><div class="metric"><span>vLLM 指标覆盖</span><strong>{analyticsPerformance.vllmObserved} / {analyticsPerformance.requests}</strong></div></div>
<section class="panel"><h2>最近 5 个时间桶</h2><AnalyticsBucketTable rows={visibleAnalyticsBuckets} maxTokens={analyticsMaxTokens} /></section>
<section class="panel"><h2>Top 5 Drilldown</h2><AnalyticsDrilldownTable rows={visibleAnalyticsDrilldown} /></section>
<section class="panel"><h2>Top 5 汇总明细</h2><UsageTable rows={visibleUsageRows} {subjectLabel} {projectLabel} /></section>
