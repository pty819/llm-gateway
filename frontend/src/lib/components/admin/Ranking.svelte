<script lang="ts">
	import type { Inventory } from '$lib/api/types';
	import { PAGE_SIZE } from '$lib/admin-config';
	import PageTitle from '$lib/components/PageTitle.svelte';
	import Pagination from '$lib/components/Pagination.svelte';

	let {
		models,
		rankingRows,
		rankingPageRows,
		usageStart = $bindable(),
		usageEnd = $bindable(),
		rankingModel = $bindable(),
		rankingLimit = $bindable(),
		rankingPage = $bindable(),
		setUsageRange,
		onRefresh,
		loading
	}: {
		models: Inventory['models'];
		rankingRows: Inventory['ranking'];
		rankingPageRows: Inventory['ranking'];
		usageStart: string;
		usageEnd: string;
		rankingModel: string;
		rankingLimit: number;
		rankingPage: number;
		setUsageRange: (days: number) => void;
		onRefresh: () => void;
		loading: boolean;
	} = $props();
</script>

<PageTitle title={'排行榜'} subtitle={'按时间范围统计 token 用量最高的用户。'} />
<section class="panel"><div class="actions"><button class="secondary" type="button" onclick={() => setUsageRange(1 / 24)}>最近 1 小时</button><button class="secondary" type="button" onclick={() => setUsageRange(1)}>最近 1 天</button><button class="secondary" type="button" onclick={() => setUsageRange(7)}>最近 1 周</button><button class="secondary" type="button" onclick={() => setUsageRange(30)}>最近 1 月</button></div><div class="form-grid"><label>开始时间<input type="datetime-local" bind:value={usageStart} /></label><label>结束时间<input type="datetime-local" bind:value={usageEnd} /></label><label>模型筛选<select bind:value={rankingModel}><option value="">全部</option>{#each models as model}<option value={model.alias}>{model.alias}</option>{/each}</select></label><label>Top N<input type="number" bind:value={rankingLimit} min="1" max="100" /></label><button type="button" onclick={onRefresh} disabled={loading}>{loading ? '查询中' : '查询'}</button></div></section>
<section class="panel"><div class="table-wrap"><table><thead><tr><th>#</th><th>用户 / Subject</th><th>请求数</th><th>输入 token</th><th>输出 token</th><th>总 token</th></tr></thead><tbody>{#each rankingPageRows as row, i}<tr><td>{(rankingPage - 1) * PAGE_SIZE.ranking + i + 1}</td><td>{row.subject_name} / {row.login_username ?? row.subject_id}</td><td>{row.request_count}</td><td>{row.prompt_tokens}</td><td>{row.completion_tokens}</td><td>{row.total_tokens}</td></tr>{:else}<tr><td colspan="6" class="empty">暂无用量数据。</td></tr>{/each}</tbody></table></div><Pagination total={rankingRows.length} page={rankingPage} size={PAGE_SIZE.ranking} onPage={(page) => (rankingPage = page)} /></section>
