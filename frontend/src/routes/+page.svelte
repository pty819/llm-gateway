<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import {
		KeyRound,
		LogOut,
		Moon,
		Package,
		Plug,
		Plus,
		RefreshCw,
		Search,
		SlidersHorizontal,
		Sun
	} from 'lucide-svelte';
	import { AdminApiClient, isApiError } from '$lib/api/client';
	import type {
		AuditEvent,
		AuthProfile,
		Diagnostics,
		GatewayKey,
		GatewayKeyCreateResponse,
		HealthCheckConfig,
		Inventory,
		IPPolicyMode,
		KeyOption,
		LoginResponse,
		ManagedRankingRow,
		ModelOption,
		OwnUsageSummary,
		PaginatedResponse,
		Project,
		ProjectMembership,
		ProjectOption,
		ReadyStatus,
		RegisterResponse,
		ResourceState,
		RuntimeMetricsSnapshot,
		Subject,
		SubjectOption,
		SubjectRateOverride,
		SubjectRateOverrideMap,
		SubjectType,
		Team,
		TeamMembership,
		TeamMemberQuotaUsage,
		TeamOption,
		TeamTokenQuotaRow,
		UpstreamHealth,
		UpstreamOptionRow
	} from '$lib/api/types';
	import StateBadge from '$lib/components/StateBadge.svelte';
	import JsonViewer from '$lib/components/JsonViewer.svelte';
	import CommandBlock from '$lib/components/CommandBlock.svelte';
	import SecretOnceDialog from '$lib/components/SecretOnceDialog.svelte';
	import PageTitle from '$lib/components/PageTitle.svelte';
	import Pagination from '$lib/components/Pagination.svelte';
	import AuditTable from '$lib/components/AuditTable.svelte';
	import UsageTable from '$lib/components/UsageTable.svelte';
	import AnalyticsBucketTable from '$lib/components/AnalyticsBucketTable.svelte';
	import AnalyticsDrilldownTable from '$lib/components/AnalyticsDrilldownTable.svelte';
	import UpstreamTable from '$lib/components/UpstreamTable.svelte';
	import CopyValue from '$lib/components/CopyValue.svelte';
	import AuthScreen from '$lib/components/AuthScreen.svelte';
	import OwnedDashboard from '$lib/components/OwnedDashboard.svelte';
	import SkillMarketSection from '$lib/components/SkillMarketSection.svelte';
	import McpMarketSection from '$lib/components/McpMarketSection.svelte';
	import Drawer from '$lib/components/Drawer.svelte';
	import ConfirmModal from '$lib/components/ConfirmModal.svelte';
	import RowMenu from '$lib/components/RowMenu.svelte';
	import Switch from '$lib/components/Switch.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import Toast from '$lib/components/Toast.svelte';
	import TrafficRibbon from '$lib/components/TrafficRibbon.svelte';
	import Sparkline from '$lib/components/Sparkline.svelte';
	import QuotaChips from '$lib/components/QuotaChips.svelte';
	import Segmented from '$lib/components/Segmented.svelte';
	import {
		clearStoredSessionToken,
		loadStoredSessionToken,
		persistSessionToken
	} from '$lib/state/admin-token';
	import { toastError, toastSuccess } from '$lib/state/toast';
	import { fmtNumber, fmtPercent } from '$lib/format';
	import { parseCidrList, parseJsonObject, validateCidrList, validateHttpUrl } from '$lib/validators';
	import {
		emptyNameCache,
		mergeEntitlementRows,
		mergeKeyRefs,
		mergeModelRefs,
		mergeModelRows,
		mergeModelTeamGrantRows,
		mergeOwnerRefs,
		mergeProjectMembershipRows,
		mergeProjects,
		mergeQuotaRows,
		mergeRatePolicyRows,
		mergeSubjectRows,
		mergeTeamMembershipRows,
		mergeTeamRows,
		mergeUpstreamRows,
		mergeUsageRows,
		type NameCache
	} from '$lib/name-cache';
	import {
		UPSTREAM_FORMAT_LABEL,
		UPSTREAM_FORMAT_SHORT_LABEL,
		composeLitellmModel,
		deriveUpstreamFormat,
		type UpstreamFormat
	} from '$lib/upstream-format';
	import {
		PAGE_SIZE,
		sections,
		navGroups,
		employeeIdPattern,
		short,
		msLabel,
		ratioLabel,
		tokenRateLabel,
		metricsKindLabel,
		bytesLabel,
		subjectTypeLabel,
		scopeLabel,
		subjectDisplay,
		pageRows,
		toDateTimeLocal,
		datetimeLocalToUtcIso,
		usageRangeForDays,
		defaultUsageRange,
		inferGatewayBaseUrl,
		clean,
		errorMessage,
		subjectLabel as subjectLabelConfig,
		membershipSubjectLabel as membershipSubjectLabelConfig,
		projectLabel as projectLabelConfig,
		keyLabel as keyLabelConfig,
		modelLabel as modelLabelConfig,
		teamLabel as teamLabelConfig,
		type LabelContext
	} from '$lib/admin-config';

	type Section = {
		id: string;
		label: string;
		group: string;
		icon: typeof KeyRound;
	};

	let active = $state('usage');
	let sessionToken = $state('');
	let rememberSession = $state(true);
	let connected = $state(false);
	let loading = $state(false);
	let pageError = $state('');
	let plaintextKey = $state('');
	let ready = $state<ReadyStatus | null>(null);
	let diagnostics = $state<Diagnostics | null>(null);
	let healthCheckConfig = $state<HealthCheckConfig | null>(null);
	let healthCheckToggling = $state(false);
	let realtime = $state<RuntimeMetricsSnapshot | null>(null);
	let realtimeStatus = $state('未连接');
	let realtimeAbort: AbortController | null = null;
	let realtimeLocked = $state(false);
	let profile = $state<AuthProfile | null>(null);
	let inventory = $state<Inventory>(emptyInventory());
	let healthResults = $state<Record<string, UpstreamHealth | string>>({});
	let usageStart = $state('');
	let usageEnd = $state('');
	let usageRangeKey = $state('1w');
	let analyticsBucket = $state<'minute' | 'hour' | 'day'>('hour');
	let analyticsDimension = $state<'model' | 'subject' | 'project' | 'endpoint' | 'outcome' | 'streaming'>('model');
	let usageFilterOpen = $state(false);
	let ownUsageStart = $state('');
	let ownUsageEnd = $state('');
	let ownUsage = $state<OwnUsageSummary | null>(null);
	let managedUsage = $state<OwnUsageSummary | null>(null);
	let managedUsageScope = $state<'project' | 'team'>('project');
	let managedUsageResourceId = $state('');
	let managedRanking = $state<ManagedRankingRow[]>([]);
	let managedRankingStart = $state('');
	let managedRankingEnd = $state('');
	let managedRankingModel = $state('');
	let managedRankingLimit = $state(20);
	let managedSubjectSearch = $state('');
	let managedSubjectCandidates = $state<Subject[]>([]);
	let managedRoles = $state<{ value: string; label: string }[]>([
		{ value: 'member', label: 'member' },
		{ value: 'manager', label: 'manager' }
	]);
	let managedProjectMemberships = $state<ProjectMembership[]>([]);
	let managedTeamMemberships = $state<TeamMembership[]>([]);
	let rankingLimit = $state(50);
	let rankingModel = $state('');
	let gatewayBaseUrl = $state('');
	let copiedItem = $state('');
	let auditDetail = $state<AuditEvent | null>(null);
	let subjectSearch = $state('');
	let subjectPasswordSearch = $state('');
	let projectOwnerSearch = $state('');
	let projectMemberSearch = $state('');
	let keySubjectSearch = $state('');
	let teamSubjectSearch = $state('');
	let entitlementSubjectSearch = $state('');
	let rateSubjectSearch = $state('');
	let usageSubjectSearch = $state('');
	let subjectPage = $state(1);
	let projectPage = $state(1);
	let projectSearch = $state('');
	let keyPage = $state(1);
	let keyListSubjectSearch = $state('');
	let keyProjectFilter = $state('');
	let keyStateFilter = $state('');
	let teamMembershipPage = $state(1);
	let teamMembershipTeamFilter = $state('');
	let teamMembershipSubjectSearch = $state('');
	let teamMembershipRoleFilter = $state('');
	let teamMembershipStateFilter = $state('');
	let auditPage = $state(1);
	let rankingPage = $state(1);
	let listPageSize = $state<number>(PAGE_SIZE.defaultList);
	let cidrEditorModel = $state<Inventory['models'][number] | null>(null);
	let cidrEditorValue = $state('');
	let realNameForm = $state({ full_name: '' });
	let realNameError = $state('');

	// ---- 服务端分页 / 搜索下推状态(全量加载改造) ----
	let modelSearch = $state('');
	let modelPage = $state(1);
	let upstreamSearch = $state('');
	let upstreamPage = $state(1);
	let teamSearch = $state('');
	let teamPage = $state(1);
	let membershipProjectFilter = $state('');
	let membershipPage = $state(1);
	let entitlementModelFilter = $state('');
	let entitlementPage = $state(1);
	let grantTeamFilter = $state('');
	let grantPage = $state(1);
	let rateScopeFilter = $state('');
	let ratePage = $state(1);
	// 名称缓存:各列表当前页随行名称 + /options 轻量端点,替代全量 inventory 查找
	let nameCache = $state<NameCache>(emptyNameCache());
	// 可搜索下拉的服务端选项(按查询词缓存)
	let subjectOptionsByQuery = $state<Record<string, SubjectOption[]>>({});
	let projectOptions = $state<ProjectOption[]>([]);
	let modelOptions = $state<ModelOption[]>([]);
	let teamOptions = $state<TeamOption[]>([]);
	let keyOptions = $state<KeyOption[]>([]);
	// realtime 锁定视图用的活动上游清单(轻量 options 端点)
	let upstreamOptions = $state<UpstreamOptionRow[]>([]);
	// 权限组抽屉 / 项目详情抽屉的按需成员数据
	let teamDrawerMembers = $state<TeamMembership[]>([]);
	let teamDrawerGrants = $state<Inventory['modelTeamGrants']>([]);
	let teamDrawerQuotaUsage = $state<TeamMemberQuotaUsage | null>(null);
	let detailMembers = $state<ProjectMembership[]>([]);
	let subjectOptionsTimer: ReturnType<typeof setTimeout> | null = null;

	// ---- 新交互状态:抽屉 / 确认弹层 / 详情(设计稿 P2/P3) ----
	let subjectDrawerOpen = $state(false);
	let projectDrawerOpen = $state(false);
	let projectMemberDrawerOpen = $state(false);
	let keyDrawerOpen = $state(false);
	let teamDrawerOpen = $state(false);
	let entitlementDrawerOpen = $state(false);
	let rateDrawerOpen = $state(false);
	let modelDrawerOpen = $state(false);
	let upstreamDrawerOpen = $state(false);
	let teamDrawerTab = $state<'members' | 'grants' | 'quota'>('members');
	let rateEditor = $state<{ id: string; name: string; rpm: string; concurrency: string } | null>(null);
	let passwordReset = $state<{ id: string; name: string; password: string } | null>(null);
	let ttlEditor = $state<{ id: string; alias: string; value: string } | null>(null);
	let detail = $state<{ kind: 'subject' | 'project' | 'key' | 'team' | 'model'; id: string } | null>(null);
	let confirmState = $state<{ title: string; message: string; confirmLabel: string; action: () => void | Promise<void> } | null>(null);
	let textEditor = $state<{ title: string; label: string; value: string; multiline?: boolean; onSave: (value: string) => void } | null>(null);
	let theme = $state<'light' | 'dark'>('light');

	let subjectForm = $state({ name: '', login_username: '', password: '', type: 'user' as SubjectType, notes: '' });
	let loginForm = $state({ username: '', password: '' });
	let registerForm = $state({ username: '', full_name: '', password: '' });
	let ownPasswordForm = $state({ current_password: '', new_password: '' });
	let ownKeyForm = $state({ name: '个人密钥' });
	let managedProjectMemberForm = $state({ resource_id: '', subject_id: '', role: 'member' });
	let managedTeamMemberForm = $state({ resource_id: '', subject_id: '', role: 'member' });
	let subjectPasswordForm = $state({ subject_id: '', new_password: '' });
	let projectForm = $state({ name: '', owner_subject_id: '', notes: '' });
	let membershipForm = $state({ project_id: '', subject_id: '', role: 'member' });
	let teamForm = $state({ name: '', notes: '' });
	let teamMembershipForm = $state({ team_id: '', subject_id: '', role: 'member' });
	let modelTeamGrantForm = $state({ model_alias_id: '', team_id: '' });
	let teamQuotaForm = $state({ team_id: '', morning: '', afternoon: '', evening: '' });
	let keyForm = $state({ subject_id: '', project_id: '', name: '' });
	let modelForm = $state({
		alias: '',
		upstream_model_name: '',
		upstream_format: 'openai' as UpstreamFormat,
		supports_streaming: true,
		supports_tools: true,
		supports_reasoning: true,
		sticky_ttl_seconds: 1200,
		ip_policy_mode: 'all_pass' as IPPolicyMode,
		ip_allowlist_cidrs: '',
		notes: ''
	});
	let upstreamForm = $state({
		model_alias_id: '',
		name: '',
		base_url: '',
		metrics_url: '',
		api_key_ref: '',
		api_key_value: '',
		health_path: '/models',
		extra_headers: '{}'
	});
	let entitlementForm = $state({ model_alias_id: '', scope: 'project', scope_id: '' });
	let rateForm = $state({
		scope: 'key',
		scope_id: '',
		requests_per_minute: '',
		concurrency_limit: ''
	});
	const api = $derived(new AdminApiClient('', sessionToken));
	const isAdmin = $derived(Boolean(profile?.subject.is_admin));
	const mustProvideRealName = $derived(Boolean(profile?.subject.requires_real_name));
	const managedProjects = $derived(profile?.managed?.projects ?? []);
	const managedTeams = $derived(profile?.managed?.teams ?? []);
	const hasManagedResources = $derived(managedProjects.length > 0 || managedTeams.length > 0);
	const marketTeams = $derived(profile?.team_memberships ?? []);
	const gatewayOrigin = $derived((gatewayBaseUrl || '').replace(/\/+$/, ''));
	const gatewayV1Base = $derived(`${gatewayOrigin}/v1`);
	const responsesEndpoint = $derived(`${gatewayV1Base}/responses`);
	const messagesEndpoint = $derived(`${gatewayV1Base}/messages`);
	const preferredModel = $derived(profile?.models[0] ?? '<model-alias>');
	const visibleKeyHint = $derived(profile?.keys[0]?.key_prefix ? `${profile.keys[0].key_prefix}...` : 'gw-...');
	const codexEnvCommand = $derived(`export LLM_GATEWAY_API_KEY="<粘贴你的完整网关密钥>"`);
	const claudeEnvCommand = $derived(
		[
			`export ANTHROPIC_BASE_URL="${gatewayOrigin}"`,
			`export ANTHROPIC_AUTH_TOKEN="<粘贴你的完整网关密钥>"`,
			`export ANTHROPIC_MODEL="${preferredModel}"`,
			`export ANTHROPIC_CUSTOM_MODEL_OPTION="${preferredModel}"`,
			`export ANTHROPIC_CUSTOM_MODEL_OPTION_NAME="${preferredModel}"`
		].join('\n')
	);
	const codexConfigCommand = $derived(
		[
			'#:schema https://developers.openai.com/codex/config-schema.json',
			`model = "${preferredModel}"`,
			'model_provider = "llm-gateway"',
			'',
			'[model_providers.llm-gateway]',
			'name = "LLM Gateway"',
			`base_url = "${gatewayV1Base}"`,
			'env_key = "LLM_GATEWAY_API_KEY"',
			'wire_api = "responses"'
		].join('\n')
	);
	const usageRows = $derived(
		inventory.usage.filter((row) => {
			if (modelFilter && row.model_alias !== modelFilter) return false;
			if (subjectFilter && row.subject_id !== subjectFilter) return false;
			if (projectFilter && row.project_id !== projectFilter) return false;
			return true;
		})
	);
	let modelFilter = $state('');
	let subjectFilter = $state('');
	let projectFilter = $state('');
	const labelCtx = $derived<LabelContext>({
		subjects: nameCache.subjects,
		managedSubjectCandidates,
		selfSubjectId: profile?.subject.id,
		selfSubject: profile?.subject ?? null,
		projects: nameCache.projects,
		keys: nameCache.keys,
		models: nameCache.models,
		teams: nameCache.teams
	});

	const totals = $derived(
		{
			requests: Number(inventory.usageTotals?.request_count ?? 0),
			prompt: Number(inventory.usageTotals?.prompt_tokens ?? 0),
			completion: Number(inventory.usageTotals?.completion_tokens ?? 0),
			total: Number(inventory.usageTotals?.total_tokens ?? 0),
			success: Number(inventory.usageTotals?.success_count ?? 0),
			failure: Number(inventory.usageTotals?.failure_count ?? 0)
		}
	);
	const successRatio = $derived(totals.requests > 0 ? totals.success / totals.requests : null);
	// 流量带 / KPI sparkline:时间正序的桶序列
	const chronoBuckets = $derived([...inventory.analyticsBuckets].reverse());
	const sparkRequests = $derived(chronoBuckets.map((row) => Number(row.request_count ?? 0)));
	const sparkTokens = $derived(chronoBuckets.map((row) => Number(row.total_tokens ?? 0)));
	const sparkSuccess = $derived(
		chronoBuckets.map((row) => (Number(row.request_count ?? 0) > 0 ? Number(row.success_count ?? 0) / Number(row.request_count) : 0))
	);
	const sparkLatency = $derived(chronoBuckets.map((row) => Number(row.avg_latency_ms ?? 0)));
	const visibleUsageRows = $derived(
		usageRows.toSorted((a, b) => Number(b.total_tokens ?? 0) - Number(a.total_tokens ?? 0)).slice(0, PAGE_SIZE.usagePreview)
	);
	const visibleAnalyticsBuckets = $derived(inventory.analyticsBuckets.slice(0, PAGE_SIZE.usagePreview));
	const visibleAnalyticsDrilldown = $derived(inventory.analyticsDrilldown.slice(0, PAGE_SIZE.usagePreview));
	const realtimeRows = $derived.by(() => {
		const live = realtime?.upstreams ?? [];
		if (!realtimeLocked) return live;
		// 锁定:全部活动配置端点按名排序,合并 realtime 指标(无数据则填占位行)
		return upstreamOptions
			.filter((u) => u.state === 'active')
			.toSorted((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN'))
			.map((u) => {
				const match = live.find((r) => r.upstream_id === u.id);
				return (
					match ?? {
						upstream_id: u.id,
						upstream_name: u.name,
						model_alias: u.model_alias ?? '',
						tokens_per_second: null,
						recent_tokens: null,
						active_connections: 0
					}
				);
			});
	});
	const realtimeUpdatedLabel = $derived(realtime ? new Date(realtime.generated_at).toLocaleTimeString() : '无');
	const analyticsMaxTokens = $derived(
		Math.max(1, ...visibleAnalyticsBuckets.map((row) => Number(row.total_tokens ?? 0)))
	);
	const rankingRows = $derived(inventory.ranking.slice(0, PAGE_SIZE.ranking));
	const rankingPageRows = $derived(pageRows(rankingRows, rankingPage, PAGE_SIZE.ranking));
	const analyticsPerformance = $derived(
		{
			requests: Number(inventory.usageTotals?.request_count ?? 0),
			retry: Number(inventory.usageTotals?.retry_count ?? 0),
			fallback: Number(inventory.usageTotals?.fallback_count ?? 0),
			vllmObserved: Number(inventory.usageTotals?.vllm_metrics_count ?? 0),
			latencyTotal: Number(inventory.usageTotals?.avg_latency_ms ?? 0),
			latencyWeight: inventory.usageTotals?.avg_latency_ms == null ? 0 : 1,
			ttftTotal: Number(inventory.usageTotals?.avg_ttft_ms ?? 0),
			ttftWeight: inventory.usageTotals?.avg_ttft_ms == null ? 0 : 1
		}
	);
	// 上下文栏标题(设计稿 L6:页面标题上移,切换页面时是不动锚点)
	const activeSection = $derived(sections.find((section) => section.id === active));
	const topbarTitle = $derived(
		isAdmin
			? (activeSection?.label ?? '控制台')
			: active === 'skill-market'
				? 'Skill 市场'
				: active === 'mcp-market'
					? 'MCP 市场'
					: '我的访问权限'
	);
	const topbarSubtitle = $derived(
		isAdmin ? (activeSection?.group ?? '') : active === 'skill-market' || active === 'mcp-market' ? '市场' : '账号'
	);
	// 详情抽屉实体查找
	const detailSubject = $derived(detail?.kind === 'subject' ? inventory.subjects.find((item) => item.id === detail?.id) : undefined);
	const detailProject = $derived(detail?.kind === 'project' ? inventory.projects.find((item) => item.id === detail?.id) : undefined);
	const detailKey = $derived(detail?.kind === 'key' ? inventory.keys.find((item) => item.id === detail?.id) : undefined);
	const detailTeam = $derived(detail?.kind === 'team' ? inventory.teams.find((item) => item.id === detail?.id) : undefined);
	const detailModel = $derived(detail?.kind === 'model' ? inventory.models.find((item) => item.id === detail?.id) : undefined);
	const detailTitle = $derived(
		detail?.kind === 'subject'
			? '用户详情'
			: detail?.kind === 'project'
				? '项目详情'
				: detail?.kind === 'key'
					? '密钥详情'
					: detail?.kind === 'team'
						? '权限组详情'
						: '模型详情'
	);
	const envClass = $derived.by(() => {
		const env = (diagnostics?.environment ?? 'local').toLowerCase();
		if (env.includes('prod')) return 'env-prod';
		if (env.includes('stag')) return 'env-staging';
		return 'env-local';
	});

	onMount(() => {
		const range = defaultUsageRange();
		usageStart = range.start;
		usageEnd = range.end;
		ownUsageStart = range.start;
		ownUsageEnd = range.end;
		gatewayBaseUrl = inferGatewayBaseUrl();
		sessionToken = loadStoredSessionToken();
		rememberSession = Boolean(sessionToken);
		if (typeof window !== 'undefined') {
			const stored = window.localStorage.getItem('gw-theme');
			if (stored === 'dark' || stored === 'light') applyTheme(stored);
		}
		void refreshReady();
		if (sessionToken) void loadProfile(true);
	});

	onDestroy(() => {
		stopRealtimeStream();
	});

	// 项目详情抽屉打开时按 project_id 拉成员(全局成员列表已分页,不再覆盖所有项目)
	$effect(() => {
		if (connected && isAdmin && detail?.kind === 'project') {
			void refreshDetailMembers().catch(() => undefined);
		}
	});

	function applyTheme(value: 'light' | 'dark') {
		theme = value;
		if (typeof document !== 'undefined') {
			document.documentElement.dataset.theme = value;
			window.localStorage.setItem('gw-theme', value);
		}
	}

	function fail(message: string) {
		pageError = message;
		toastError(message);
	}

	function askConfirm(title: string, message: string, confirmLabel: string, action: () => void | Promise<void>) {
		confirmState = { title, message, confirmLabel, action };
	}

	function closeConfirm() {
		confirmState = null;
	}

	async function runConfirm() {
		const action = confirmState?.action;
		confirmState = null;
		if (action) await action();
	}

	async function loginAccount(fromStorage = false) {
		if (!loginForm.username.trim() || !loginForm.password) {
			fail('请输入用户名和密码。');
			return;
		}
		await run(async () => {
			const response = await new AdminApiClient().post<LoginResponse>('/auth/login', loginForm);
			sessionToken = response.session_token;
			profile = response.profile;
			connected = true;
			if (!fromStorage) persistSessionToken(sessionToken, rememberSession);
			if (profile.subject.is_admin) {
				const authedApi = new AdminApiClient('', sessionToken);
				diagnostics = await authedApi.get<Diagnostics>('/admin/diagnostics');
				await refreshAll();
				startRealtimeStream();
			} else {
				stopRealtimeStream();
				await refreshManagedRoles();
				await fetchOwnUsage();
			}
		});
	}

	async function registerAccount() {
		if (!employeeIdPattern.test(registerForm.username.trim())) {
			fail('工号必须是 1 个字母加 8 位数字，例如 l00014624。');
			return;
		}
		if (!registerForm.full_name.trim() || registerForm.password.length < 8) {
			fail('请输入真实姓名，密码至少 8 个字符。');
			return;
		}
		await run(async () => {
			const response = await new AdminApiClient().post<RegisterResponse>('/auth/register', registerForm);
			sessionToken = response.session_token;
			profile = response.profile;
			plaintextKey = response.gateway_key.plaintext_key;
			connected = true;
			persistSessionToken(sessionToken, rememberSession);
			registerForm = { username: '', full_name: '', password: '' };
			await refreshManagedRoles();
			await fetchOwnUsage();
		});
	}

	async function loadProfile(fromStorage = false) {
		await run(async () => {
			profile = await api.get<AuthProfile>('/auth/me');
			connected = true;
			if (!fromStorage) persistSessionToken(sessionToken, rememberSession);
			if (profile.subject.is_admin) {
				diagnostics = await api.get<Diagnostics>('/admin/diagnostics');
				await refreshAll();
				startRealtimeStream();
			} else {
				stopRealtimeStream();
				await refreshManagedRoles();
				await fetchOwnUsage();
			}
		});
	}

	function disconnect() {
		stopRealtimeStream();
		sessionToken = '';
		profile = null;
		connected = false;
		plaintextKey = '';
		copiedItem = '';
		pageError = '';
		clearStoredSessionToken();
		inventory = emptyInventory();
	}

	function startRealtimeStream() {
		stopRealtimeStream();
		if (!sessionToken || typeof window === 'undefined') return;
		const controller = new AbortController();
		realtimeAbort = controller;
		realtimeStatus = '连接中';
		void consumeRealtimeStream(controller);
	}

	function stopRealtimeStream() {
		if (realtimeAbort) {
			realtimeAbort.abort();
			realtimeAbort = null;
		}
		realtimeStatus = '未连接';
	}

	async function consumeRealtimeStream(controller: AbortController) {
		try {
			const response = await fetch('/admin/realtime/stream?window_seconds=10&interval_seconds=1', {
				headers: { 'x-session-token': sessionToken },
				signal: controller.signal
			});
			if (!response.ok || !response.body) throw new Error(`实时指标连接失败: HTTP ${response.status}`);
			realtimeStatus = '已连接';
			const reader = response.body.getReader();
			const decoder = new TextDecoder();
			let buffer = '';
			while (true) {
				const { value, done } = await reader.read();
				if (done) break;
				buffer += decoder.decode(value, { stream: true });
				let boundary = buffer.indexOf('\n\n');
				while (boundary !== -1) {
					const block = buffer.slice(0, boundary);
					buffer = buffer.slice(boundary + 2);
					consumeRealtimeEvent(block);
					boundary = buffer.indexOf('\n\n');
				}
			}
			if (!controller.signal.aborted) realtimeStatus = '已断开';
		} catch (error) {
			if (!controller.signal.aborted) realtimeStatus = errorMessage(error);
		} finally {
			if (realtimeAbort === controller) realtimeAbort = null;
		}
	}

	function consumeRealtimeEvent(block: string) {
		const data = block
			.split('\n')
			.filter((line) => line.startsWith('data:'))
			.map((line) => line.slice(5).trimStart())
			.join('\n');
		if (!data) return;
		realtime = JSON.parse(data) as RuntimeMetricsSnapshot;
	}

	async function refreshReady() {
		try {
			const response = await fetch('/health/ready');
			ready = (await response.json()) as ReadyStatus;
		} catch {
			ready = null;
		}
	}

	async function toggleHealthCheck() {
		if (!healthCheckConfig || healthCheckToggling) return;
		const next = !healthCheckConfig.enabled;
		healthCheckToggling = true;
		await run(async () => {
			healthCheckConfig = await api.setHealthCheckConfig(next);
		}, '健康巡检配置已更新');
		healthCheckToggling = false;
	}

	// ---- 服务端分页取数:每个列表只拉当前页,搜索/筛选下推为查询参数 ----

	function listOffset(page: number): number {
		return (Math.max(1, page) - 1) * listPageSize;
	}

	async function fetchSubjects() {
		const page = await api.get<PaginatedResponse<Subject>>('/admin/subjects', {
			q: subjectSearch.trim() || undefined,
			limit: listPageSize,
			offset: listOffset(subjectPage)
		});
		nameCache = mergeSubjectRows(nameCache, page.items);
		inventory = { ...inventory, subjects: page.items, subjectsTotal: page.total };
		// 限流覆盖只取当前页用户(MGET),不再整库 SCAN
		const overrides = page.items.length
			? await api.get<SubjectRateOverrideMap>('/admin/subjects/rate-overrides', {
				subject_ids: page.items.map((row) => row.id).join(',')
			})
			: {};
		inventory = { ...inventory, subjectRateOverrides: overrides };
	}

	async function fetchProjects() {
		const page = await api.get<PaginatedResponse<Project>>('/admin/projects', {
			q: projectSearch.trim() || undefined,
			limit: listPageSize,
			offset: listOffset(projectPage)
		});
		nameCache = mergeOwnerRefs(nameCache, page.items);
		inventory = { ...inventory, projects: page.items, projectsTotal: page.total };
	}

	async function fetchMemberships() {
		const page = await api.get<PaginatedResponse<ProjectMembership>>('/admin/project-memberships', {
			project_id: membershipProjectFilter || undefined,
			limit: listPageSize,
			offset: listOffset(membershipPage)
		});
		nameCache = mergeProjectMembershipRows(nameCache, page.items);
		inventory = { ...inventory, memberships: page.items, membershipsTotal: page.total };
	}

	async function fetchKeys() {
		const page = await api.get<PaginatedResponse<GatewayKey>>('/admin/gateway-keys', {
			q: keyListSubjectSearch.trim() || undefined,
			project_id: keyProjectFilter || undefined,
			state: keyStateFilter || undefined,
			limit: listPageSize,
			offset: listOffset(keyPage)
		});
		nameCache = mergeKeyRefs(nameCache, page.items);
		inventory = { ...inventory, keys: page.items, keysTotal: page.total };
	}

	async function fetchModels() {
		const page = await api.get<PaginatedResponse<Inventory['models'][number]>>('/admin/model-aliases', {
			q: modelSearch.trim() || undefined,
			limit: listPageSize,
			offset: listOffset(modelPage)
		});
		nameCache = mergeModelRows(nameCache, page.items);
		inventory = { ...inventory, models: page.items, modelsTotal: page.total };
	}

	async function fetchUpstreams() {
		const page = await api.get<PaginatedResponse<Inventory['upstreams'][number]>>('/admin/upstreams', {
			q: upstreamSearch.trim() || undefined,
			limit: listPageSize,
			offset: listOffset(upstreamPage)
		});
		nameCache = mergeUpstreamRows(nameCache, page.items);
		inventory = { ...inventory, upstreams: page.items, upstreamsTotal: page.total };
	}

	async function fetchEntitlements() {
		const page = await api.get<PaginatedResponse<Inventory['entitlements'][number]>>('/admin/model-entitlements', {
			model_alias_id: entitlementModelFilter || undefined,
			limit: listPageSize,
			offset: listOffset(entitlementPage)
		});
		nameCache = mergeEntitlementRows(nameCache, page.items);
		inventory = { ...inventory, entitlements: page.items, entitlementsTotal: page.total };
	}

	async function fetchModelTeamGrants() {
		const page = await api.get<PaginatedResponse<Inventory['modelTeamGrants'][number]>>('/admin/model-team-grants', {
			team_id: grantTeamFilter || undefined,
			limit: listPageSize,
			offset: listOffset(grantPage)
		});
		nameCache = mergeModelTeamGrantRows(nameCache, page.items);
		inventory = { ...inventory, modelTeamGrants: page.items, modelTeamGrantsTotal: page.total };
	}

	async function fetchTeams() {
		const page = await api.get<PaginatedResponse<Team>>('/admin/teams', {
			q: teamSearch.trim() || undefined,
			limit: listPageSize,
			offset: listOffset(teamPage)
		});
		nameCache = mergeTeamRows(nameCache, page.items);
		inventory = { ...inventory, teams: page.items, teamsTotal: page.total };
		// 配额行只取当前页团队,QuotaChips 直接可用
		const quotas = page.items.length
			? await api.get<PaginatedResponse<TeamTokenQuotaRow>>('/admin/team-token-quotas', {
				team_ids: page.items.map((row) => row.id).join(','),
				limit: page.items.length
			})
			: { items: [] as TeamTokenQuotaRow[] };
		nameCache = mergeQuotaRows(nameCache, quotas.items);
		inventory = { ...inventory, teamTokenQuotas: quotas.items };
	}

	async function fetchTeamMemberships() {
		const page = await api.get<PaginatedResponse<TeamMembership>>('/admin/team-memberships', {
			q: teamMembershipSubjectSearch.trim() || undefined,
			team_id: teamMembershipTeamFilter || undefined,
			state: teamMembershipStateFilter || undefined,
			role: teamMembershipRoleFilter.trim() || undefined,
			limit: listPageSize,
			offset: listOffset(teamMembershipPage)
		});
		nameCache = mergeTeamMembershipRows(nameCache, page.items);
		inventory = { ...inventory, teamMemberships: page.items, teamMembershipsTotal: page.total };
	}

	async function fetchRatePolicies() {
		const page = await api.get<PaginatedResponse<Inventory['ratePolicies'][number]>>('/admin/rate-policies', {
			scope: rateScopeFilter || undefined,
			limit: listPageSize,
			offset: listOffset(ratePage)
		});
		nameCache = mergeRatePolicyRows(nameCache, page.items);
		inventory = { ...inventory, ratePolicies: page.items, ratePoliciesTotal: page.total };
	}

	async function fetchAudit() {
		const page = await api.get<PaginatedResponse<AuditEvent>>('/admin/audit-events', {
			limit: PAGE_SIZE.audit,
			offset: (Math.max(1, auditPage) - 1) * PAGE_SIZE.audit
		});
		inventory = { ...inventory, audit: page.items, auditTotal: page.total };
	}

	async function fetchUpstreamOptions() {
		upstreamOptions = await api.get<UpstreamOptionRow[]>('/admin/upstreams/options', { limit: 1000 });
	}

	async function fetchSubjectOptions(query: string) {
		const rows = await api.get<SubjectOption[]>('/admin/subjects/options', {
			q: query.trim() || undefined,
			limit: PAGE_SIZE.selectOptions
		});
		nameCache = mergeSubjectRows(nameCache, rows);
		subjectOptionsByQuery = { ...subjectOptionsByQuery, [query]: rows };
	}

	function queueSubjectOptions(query: string) {
		if (subjectOptionsTimer) clearTimeout(subjectOptionsTimer);
		subjectOptionsTimer = setTimeout(() => {
			void fetchSubjectOptions(query).catch(() => undefined);
		}, 250);
	}

	async function loadBaseOptions() {
		await Promise.all([
			fetchSubjectOptions(''),
			(async () => {
				projectOptions = await api.get<ProjectOption[]>('/admin/projects/options', {
					exclude_name_prefix: 'user-',
					limit: 50
				});
				nameCache = mergeProjects(nameCache, projectOptions);
			})().catch(() => undefined),
			(async () => {
				modelOptions = await api.get<ModelOption[]>('/admin/model-aliases/options', { limit: 50 });
				nameCache = mergeModelRows(nameCache, modelOptions);
			})().catch(() => undefined),
			(async () => {
				teamOptions = await api.get<TeamOption[]>('/admin/teams/options', { limit: 200 });
				nameCache = mergeTeamRows(nameCache, teamOptions);
			})().catch(() => undefined)
		]);
	}

	async function fetchKeyOptions() {
		keyOptions = await api.get<KeyOption[]>('/admin/gateway-keys/options', { limit: 50 });
		nameCache = mergeKeyRefs(nameCache, keyOptions);
	}

	async function refreshAll() {
		await run(async () => {
			await refreshReady();
			if (!isAdmin) {
				profile = await api.get<AuthProfile>('/auth/me');
				await refreshManagedRoles();
				await fetchOwnUsage();
				return;
			}
			healthCheckConfig = await api.get<HealthCheckConfig>('/admin/health-check');
			await Promise.all([
				fetchSubjects(),
				fetchProjects(),
				fetchKeys(),
				fetchModels(),
				fetchUpstreams(),
				fetchModelTeamGrants(),
				fetchTeams(),
				fetchTeamMemberships(),
				fetchRatePolicies(),
				fetchAudit(),
				fetchUpstreamOptions(),
				loadBaseOptions()
			]);
		});
	}

	async function refreshUsageAnalytics() {
		usageFilterOpen = false;
		await run(async () => {
			const analyticsParams = {
				start: datetimeLocalToUtcIso(usageStart),
				end: datetimeLocalToUtcIso(usageEnd),
				model: modelFilter,
				subject_id: subjectFilter,
				project_id: projectFilter
			};
			const [usageTotals, usage, buckets, drilldown] = await Promise.all([
				api.get<Inventory['usageTotals']>('/admin/usage/totals', analyticsParams),
				api.get<Inventory['usage']>('/admin/usage/summary', {
					...analyticsParams,
					limit: PAGE_SIZE.usagePreview
				}),
				api.get<Inventory['analyticsBuckets']>('/admin/analytics/time-buckets', {
					...analyticsParams,
					bucket: analyticsBucket,
					limit: PAGE_SIZE.bucketRibbon
				}),
				api.get<Inventory['analyticsDrilldown']>('/admin/analytics/drilldown', {
					...analyticsParams,
					dimension: analyticsDimension,
					limit: PAGE_SIZE.usagePreview
				})
			]);
			nameCache = mergeUsageRows(nameCache, usage);
			inventory = { ...inventory, usageTotals, usage, analyticsBuckets: buckets, analyticsDrilldown: drilldown };
		});
	}

	async function refreshUsageRanking() {
		await run(async () => {
			const ranking = await api.get<Inventory['ranking']>('/admin/usage/ranking', {
				start: datetimeLocalToUtcIso(usageStart),
				end: datetimeLocalToUtcIso(usageEnd),
				model: rankingModel,
				limit: rankingLimit
			});
			inventory = { ...inventory, ranking };
		});
	}

	async function createSubject() {
		if (subjectForm.login_username && !employeeIdPattern.test(subjectForm.login_username.trim())) {
			fail('工号必须是 1 个字母加 8 位数字，例如 l00014624。');
			return;
		}
		await run(async () => {
			await api.post('/admin/subjects', clean({ ...subjectForm }));
			subjectForm = { name: '', login_username: '', password: '', type: 'user', notes: '' };
			subjectDrawerOpen = false;
			await fetchSubjects();
		}, '用户已创建');
	}

	async function patchSubject(id: string, patch: Record<string, unknown>) {
		await run(async () => {
			await api.patch(`/admin/subjects/${id}`, patch);
			await fetchSubjects();
		}, '用户已更新');
	}

	async function setSubjectState(id: string, state: ResourceState) {
		await run(async () => {
			await api.patch(`/admin/subjects/${id}/state`, { state });
			await fetchSubjects();
		});
	}

	/** 限流覆盖编辑(抽屉表单替代原 prompt):PUT 全量合并语义保持不变。 */
	async function saveRateEditor() {
		if (!rateEditor) return;
		const parseField = (raw: string): number | null => {
			const trimmed = raw.trim();
			if (trimmed === '') return null;
			const value = Number(trimmed);
			if (!Number.isFinite(value) || value <= 0 || !Number.isInteger(value)) throw new Error('上限必须是正整数。');
			return value;
		};
		try {
			const merged: SubjectRateOverride = {
				rpm: parseField(rateEditor.rpm),
				concurrency: parseField(rateEditor.concurrency)
			};
			const target = rateEditor.id;
			await run(async () => {
				await api.put(`/admin/subjects/${target}/rate-override`, merged);
				await fetchSubjects();
			}, '限流覆盖已保存');
			rateEditor = null;
		} catch (error) {
			fail(errorMessage(error));
		}
	}

	function openRateEditor(subject: Subject) {
		const current = inventory.subjectRateOverrides[subject.id] ?? { rpm: null, concurrency: null };
		rateEditor = {
			id: subject.id,
			name: subjectDisplay(subject),
			rpm: current.rpm === null ? '' : String(current.rpm),
			concurrency: current.concurrency === null ? '' : String(current.concurrency)
		};
	}

	/** Clear a subject's per-user override entirely (both dimensions). */
	async function clearSubjectRateOverride(id: string) {
		await run(async () => {
			await api.delete(`/admin/subjects/${id}/rate-override`);
			await fetchSubjects();
		}, '限流覆盖已清除');
	}

	async function submitPasswordReset() {
		if (!passwordReset) return;
		if (passwordReset.password.length < 8) {
			fail('新密码至少 8 个字符。');
			return;
		}
		const target = passwordReset.id;
		const newPassword = passwordReset.password;
		await run(async () => {
			await api.patch(`/admin/subjects/${target}/password`, { new_password: newPassword });
		}, '密码已重置');
		passwordReset = null;
	}

	function deleteSubject(subject: Inventory['subjects'][number]) {
		askConfirm(
			'删除用户',
			`删除用户 ${subject.name}（${subject.login_username ?? '无工号'}）——该操作不可撤销，其密钥将立即失效。`,
			'删除',
			async () => {
				await run(async () => {
					await api.delete(`/admin/subjects/${subject.id}`);
					await fetchSubjects();
				}, '用户已删除');
			}
		);
	}

	async function createProject() {
		await run(async () => {
			await api.post('/admin/projects', clean({ ...projectForm }));
			projectForm = { name: '', owner_subject_id: '', notes: '' };
			projectDrawerOpen = false;
			await fetchProjects();
		}, '项目已创建');
	}

	async function patchProject(id: string, patch: Record<string, unknown>) {
		await run(async () => {
			await api.patch(`/admin/projects/${id}`, clean(patch));
			await fetchProjects();
		}, '项目已更新');
	}

	async function createMembership() {
		await run(async () => {
			await api.post('/admin/project-memberships', clean({ ...membershipForm }));
			membershipForm = { project_id: '', subject_id: '', role: 'member' };
			projectMemberDrawerOpen = false;
			await Promise.all([fetchMemberships(), refreshDetailMembers()]);
		}, '成员已添加');
	}

	async function issueKey() {
		await run(async () => {
			const response = await api.post<GatewayKeyCreateResponse>('/admin/gateway-keys', clean({ ...keyForm }));
			plaintextKey = response.plaintext_key;
			keyForm = { subject_id: '', project_id: '', name: '' };
			keyDrawerOpen = false;
			await fetchKeys();
		});
	}

	async function issueOwnKey() {
		await run(async () => {
			const response = await api.post<GatewayKeyCreateResponse>('/auth/keys', clean({ ...ownKeyForm }));
			plaintextKey = response.plaintext_key;
			ownKeyForm = { name: '个人密钥' };
			profile = await api.get<AuthProfile>('/auth/me');
			await fetchOwnUsage();
		});
	}

	async function setOwnKeyState(key: { id: string; state: string }, newState: 'active' | 'disabled') {
		await run(async () => {
			await api.patch(`/auth/keys/${key.id}/state`, { state: newState });
			profile = await api.get<AuthProfile>('/auth/me');
		});
	}

	async function changeOwnPassword() {
		if (!ownPasswordForm.current_password || ownPasswordForm.new_password.length < 8) {
			fail('请输入当前密码，新密码至少 8 个字符。');
			return;
		}
		await run(async () => {
			await api.patch('/auth/password', ownPasswordForm);
			ownPasswordForm = { current_password: '', new_password: '' };
		}, '密码已修改');
	}

	async function submitRealName() {
		realNameError = '';
		if (!realNameForm.full_name.trim()) {
			realNameError = '请填写真实姓名。';
			return;
		}
		loading = true;
		pageError = '';
		try {
			profile = await api.patch<AuthProfile>('/auth/profile', {
				full_name: realNameForm.full_name.trim()
			});
			realNameForm = { full_name: '' };
		} catch (error) {
			realNameError = errorMessage(error);
		} finally {
			loading = false;
		}
	}

	async function setKeyState(id: string, state: ResourceState) {
		await run(async () => {
			await api.patch(`/admin/gateway-keys/${id}/state`, { state });
			await fetchKeys();
		});
	}

	async function createModel() {
		const cidrCheck = modelForm.ip_policy_mode === 'allowlist' ? validateCidrList(modelForm.ip_allowlist_cidrs) : { ok: true };
		if (!cidrCheck.ok) {
			fail(cidrCheck.message ?? 'CIDR 列表不合法');
			return;
		}
		await run(async () => {
			await api.post(
				'/admin/model-aliases',
				clean({
					alias: modelForm.alias,
					upstream_model_name: modelForm.upstream_model_name,
					litellm_model: composeLitellmModel(modelForm.upstream_format, modelForm.upstream_model_name),
					supports_streaming: modelForm.supports_streaming,
					supports_tools: modelForm.supports_tools,
					supports_reasoning: modelForm.supports_reasoning,
					sticky_ttl_seconds: modelForm.sticky_ttl_seconds,
					ip_policy_mode: modelForm.ip_policy_mode,
					ip_allowlist_cidrs:
						modelForm.ip_policy_mode === 'allowlist' ? parseCidrList(modelForm.ip_allowlist_cidrs) : [],
					notes: modelForm.notes
				})
			);
			modelForm = {
				alias: '',
				upstream_model_name: '',
				upstream_format: 'openai',
				supports_streaming: true,
				supports_tools: true,
				supports_reasoning: true,
				sticky_ttl_seconds: 1200,
				ip_policy_mode: 'all_pass',
				ip_allowlist_cidrs: '',
				notes: ''
			};
			modelDrawerOpen = false;
			await Promise.all([fetchModels(), fetchUpstreamOptions(), loadBaseOptions()]);
		}, '模型别名已创建');
	}

	async function patchModel(id: string, patch: Record<string, unknown>) {
		await run(async () => {
			await api.patch(`/admin/model-aliases/${id}`, patch);
			await fetchModels();
		}, '模型已更新');
	}

	function saveTtlEditor() {
		if (!ttlEditor) return;
		const value = Number(ttlEditor.value);
		if (!Number.isFinite(value) || value <= 0) {
			fail('粘性生命周期必须是正数秒。');
			return;
		}
		void patchModel(ttlEditor.id, { sticky_ttl_seconds: value });
		ttlEditor = null;
	}

	function editModelCidrs(model: Inventory['models'][number]) {
		cidrEditorModel = model;
		cidrEditorValue = model.ip_policy_mode === 'allowlist' ? model.ip_allowlist_cidrs.join('\n') : '';
	}

	async function saveModelCidrs() {
		if (!cidrEditorModel) return;
		const trimmed = cidrEditorValue.trim();
		if (!trimmed) {
			await patchModel(cidrEditorModel.id, { ip_policy_mode: 'all_pass', ip_allowlist_cidrs: [] });
			cidrEditorModel = null;
			return;
		}
		const cidrCheck = validateCidrList(trimmed);
		if (!cidrCheck.ok) {
			fail(cidrCheck.message ?? 'CIDR 列表不合法');
			return;
		}
		await patchModel(cidrEditorModel.id, {
			ip_policy_mode: 'allowlist',
			ip_allowlist_cidrs: parseCidrList(trimmed)
		});
		cidrEditorModel = null;
	}

	function deleteModel(model: Inventory['models'][number]) {
		askConfirm('删除模型别名', `确认删除模型别名 ${model.alias}？该操作不可撤销。`, '删除', async () => {
			await run(async () => {
				try {
					await api.delete(`/admin/model-aliases/${model.id}`);
				} catch (error) {
					if (isModelUpstreamConflict(error)) {
						const upstreamCount = Number((error.detail as { upstream_count?: number }).upstream_count ?? 0);
						askConfirm(
							'存在上游依赖',
							`这个模型还有 ${upstreamCount} 个上游端点依赖。是否一起删除这些上游依赖？`,
							'一并删除',
							async () => {
								await run(async () => {
									await api.delete(`/admin/model-aliases/${model.id}`, { cascade_upstreams: true });
									await Promise.all([fetchModels(), fetchUpstreams(), fetchUpstreamOptions()]);
								}, '模型及上游依赖已删除');
							}
						);
						return;
					}
					throw error;
				}
				await fetchModels();
			}, '模型别名已删除');
		});
	}

	function deleteUpstream(upstream: Inventory['upstreams'][number]) {
		askConfirm('删除上游端点', `确认删除上游端点 ${upstream.name}？该操作不可撤销。`, '删除', async () => {
			await run(async () => {
				await api.delete(`/admin/upstreams/${upstream.id}`);
				await Promise.all([fetchUpstreams(), fetchUpstreamOptions()]);
			}, '上游端点已删除');
		});
	}

	async function createUpstream() {
		const urlCheck = validateHttpUrl(upstreamForm.base_url, '上游地址');
		if (!urlCheck.ok) {
			fail(urlCheck.message ?? '上游地址不合法');
			return;
		}
		if (upstreamForm.metrics_url.trim()) {
			const metricsUrlCheck = validateHttpUrl(upstreamForm.metrics_url, 'Metrics URL');
			if (!metricsUrlCheck.ok) {
				fail(metricsUrlCheck.message ?? 'Metrics URL 不合法');
				return;
			}
		}
		if (!upstreamForm.health_path.startsWith('/')) {
			fail('健康检查路径必须以 / 开头。');
			return;
		}
		await run(async () => {
			await api.post(
				'/admin/upstreams',
				clean({
					...upstreamForm,
					extra_headers: parseJsonObject(upstreamForm.extra_headers, '额外请求头')
				})
			);
			upstreamForm = {
				model_alias_id: '',
				name: '',
				base_url: '',
				metrics_url: '',
				api_key_ref: '',
				api_key_value: '',
				health_path: '/models',
				extra_headers: '{}'
			};
			upstreamDrawerOpen = false;
			await Promise.all([fetchUpstreams(), fetchUpstreamOptions()]);
		}, '上游端点已创建');
	}

	async function setUpstreamState(id: string, state: ResourceState) {
		await run(async () => {
			await api.patch(`/admin/upstreams/${id}`, { state });
			await Promise.all([fetchUpstreams(), fetchUpstreamOptions()]);
		});
	}

	async function patchUpstream(id: string, patch: Record<string, unknown>) {
		await run(async () => {
			await api.patch(`/admin/upstreams/${id}`, clean(patch));
			await fetchUpstreams();
		}, '上游端点已更新');
	}

	async function checkUpstream(id: string) {
		healthResults[id] = '检查中';
		try {
			healthResults[id] = await api.get<UpstreamHealth>(`/admin/upstreams/${id}/health`);
		} catch (error) {
			healthResults[id] = errorMessage(error);
		}
	}

	async function createEntitlement() {
		await run(async () => {
			await api.post(
				'/admin/model-entitlements',
				clean({
					model_alias_id: entitlementForm.model_alias_id,
					subject_id: entitlementForm.scope === 'subject' ? entitlementForm.scope_id : '',
					project_id: entitlementForm.scope === 'project' ? entitlementForm.scope_id : '',
					gateway_key_id: entitlementForm.scope === 'key' ? entitlementForm.scope_id : ''
				})
			);
			entitlementForm = { model_alias_id: '', scope: 'project', scope_id: '' };
			entitlementDrawerOpen = false;
			await fetchEntitlements();
		}, '授权已创建');
	}

	async function setEntitlementState(id: string, state: ResourceState) {
		await run(async () => {
			await api.patch(`/admin/model-entitlements/${id}/state`, { state });
			await fetchEntitlements();
		});
	}

	async function createTeam() {
		await run(async () => {
			await api.post('/admin/teams', clean({ ...teamForm }));
			teamForm = { name: '', notes: '' };
			teamDrawerOpen = false;
			await fetchTeams();
		}, '权限组已创建');
	}

	async function patchTeam(id: string, patch: Record<string, unknown>) {
		await run(async () => {
			await api.patch(`/admin/teams/${id}`, clean(patch));
			await fetchTeams();
		}, '权限组已更新');
	}

	async function createTeamMembership() {
		await run(async () => {
			await api.post('/admin/team-memberships', clean({ ...teamMembershipForm }));
			teamMembershipForm = { ...teamMembershipForm, subject_id: '' };
			await Promise.all([fetchTeamMemberships(), refreshTeamDrawerData()]);
		}, '成员已加入权限组');
	}

	async function setTeamMembershipState(id: string, state: ResourceState) {
		await run(async () => {
			await api.patch(`/admin/team-memberships/${id}/state`, { state });
			await Promise.all([fetchTeamMemberships(), refreshTeamDrawerData()]);
		});
	}

	async function createModelTeamGrant() {
		await run(async () => {
			await api.post('/admin/model-team-grants', clean({ ...modelTeamGrantForm }));
			modelTeamGrantForm = { ...modelTeamGrantForm, model_alias_id: '' };
			await Promise.all([fetchModelTeamGrants(), refreshTeamDrawerData()]);
		}, '模型已授权');
	}

	async function setModelTeamGrantState(id: string, state: ResourceState) {
		await run(async () => {
			await api.patch(`/admin/model-team-grants/${id}/state`, { state });
			await Promise.all([fetchModelTeamGrants(), refreshTeamDrawerData()]);
		});
	}

	async function saveTeamQuota() {
		if (!teamQuotaForm.team_id) return;
		await run(async () => {
			await api.put(`/admin/teams/${teamQuotaForm.team_id}/token-quota`, {
				morning_tokens: teamQuotaForm.morning.trim() === '' ? null : Number(teamQuotaForm.morning),
				afternoon_tokens: teamQuotaForm.afternoon.trim() === '' ? null : Number(teamQuotaForm.afternoon),
				evening_tokens: teamQuotaForm.evening.trim() === '' ? null : Number(teamQuotaForm.evening)
			});
			await fetchTeams();
		}, '配额已保存');
	}

	function teamQuotaRow(teamId: string): TeamTokenQuotaRow | undefined {
		return inventory.teamTokenQuotas.find((row) => row.team_id === teamId);
	}

	/** 权限组抽屉打开时按 team_id 拉成员/授权/成员配额用量(全局列表分页后不再覆盖全部团队)。 */
	async function refreshTeamDrawerData() {
		const teamId = teamMembershipForm.team_id;
		if (!teamId || detail?.kind !== 'team') return;
		const [members, grants] = await Promise.all([
			api.get<PaginatedResponse<TeamMembership>>('/admin/team-memberships', { team_id: teamId, limit: 500 }),
			api.get<PaginatedResponse<Inventory['modelTeamGrants'][number]>>('/admin/model-team-grants', { team_id: teamId, limit: 500 })
		]);
		nameCache = mergeTeamMembershipRows(nameCache, members.items);
		nameCache = mergeModelTeamGrantRows(nameCache, grants.items);
		teamDrawerMembers = members.items;
		teamDrawerGrants = grants.items;
		// 配额:上限对每个成员分别生效,当前窗口的成员已用量按成员拉取
		if (members.items.length) {
			const usage = await api
				.get<TeamMemberQuotaUsage>('/admin/teams/' + teamId + '/token-quota/member-usage', {
					subject_ids: members.items.map((m) => m.subject_id).join(',')
				})
				.catch(() => null);
			teamDrawerQuotaUsage = usage;
		} else {
			teamDrawerQuotaUsage = null;
		}
	}

	/** 项目详情抽屉打开时按 project_id 拉成员。 */
	async function refreshDetailMembers() {
		const projectId = detail?.kind === 'project' ? detail.id : '';
		if (!projectId) return;
		const page = await api.get<PaginatedResponse<ProjectMembership>>('/admin/project-memberships', {
			project_id: projectId,
			limit: 200
		});
		nameCache = mergeProjectMembershipRows(nameCache, page.items);
		detailMembers = page.items;
	}

	function openTeamDrawer(team: Team, tab: 'members' | 'grants' | 'quota' = 'members') {
		teamDrawerTab = tab;
		teamMembershipForm.team_id = team.id;
		modelTeamGrantForm.team_id = team.id;
		teamQuotaForm.team_id = team.id;
		const quota = teamQuotaRow(team.id);
		teamQuotaForm.morning = quota?.morning_tokens == null ? '' : String(quota.morning_tokens);
		teamQuotaForm.afternoon = quota?.afternoon_tokens == null ? '' : String(quota.afternoon_tokens);
		teamQuotaForm.evening = quota?.evening_tokens == null ? '' : String(quota.evening_tokens);
		detail = { kind: 'team', id: team.id };
		void refreshTeamDrawerData().catch(() => undefined);
	}

	async function createRatePolicy() {
		await run(async () => {
			await api.post(
				'/admin/rate-policies',
				clean({
					scope: rateForm.scope,
					scope_id: rateForm.scope_id,
					requests_per_minute: rateForm.requests_per_minute === '' ? null : Number(rateForm.requests_per_minute),
					concurrency_limit: rateForm.concurrency_limit === '' ? null : Number(rateForm.concurrency_limit)
				})
			);
			rateForm = { scope: 'key', scope_id: '', requests_per_minute: '', concurrency_limit: '' };
			rateDrawerOpen = false;
			await fetchRatePolicies();
		}, '限流策略已创建');
	}

	async function setRateState(id: string, state: ResourceState) {
		await run(async () => {
			await api.patch(`/admin/rate-policies/${id}`, { state });
			await fetchRatePolicies();
		});
	}

	async function refreshOwnUsage() {
		await run(fetchOwnUsage);
	}

	async function fetchOwnUsage() {
		ownUsage = await api.get<OwnUsageSummary>('/auth/usage/summary', {
			start: datetimeLocalToUtcIso(ownUsageStart),
			end: datetimeLocalToUtcIso(ownUsageEnd)
		});
	}

	async function refreshManagedSubjects() {
		await run(async () => {
			managedSubjectCandidates = await api.get<Subject[]>('/auth/managed/subjects', {
				q: managedSubjectSearch,
				limit: PAGE_SIZE.selectOptions
			});
		});
	}

	async function refreshManagedRoles() {
		if (!hasManagedResources) return;
		managedRoles = await api.get<{ value: string; label: string }[]>('/auth/managed/roles');
	}

	async function refreshManagedProjectMemberships(resourceId = managedProjectMemberForm.resource_id) {
		if (!resourceId) {
			managedProjectMemberships = [];
			return;
		}
		managedProjectMemberships = await api.get<ProjectMembership[]>('/auth/managed/project-memberships', {
			resource_id: resourceId
		});
	}

	async function refreshManagedTeamMemberships(resourceId = managedTeamMemberForm.resource_id) {
		if (!resourceId) {
			managedTeamMemberships = [];
			return;
		}
		managedTeamMemberships = await api.get<TeamMembership[]>('/auth/managed/team-memberships', {
			resource_id: resourceId
		});
	}

	async function refreshManagedUsage() {
		await run(async () => {
			managedUsage = await api.get<OwnUsageSummary>('/auth/managed/usage/summary', {
				scope: managedUsageScope,
				resource_id: managedUsageResourceId,
				start: ownUsageStart,
				end: ownUsageEnd
			});
		});
	}

	async function refreshManagedRanking() {
		if (!managedUsageResourceId) return;
		await run(async () => {
			const params: Record<string, string> = {
				scope: managedUsageScope,
				resource_id: managedUsageResourceId,
				limit: String(managedRankingLimit)
			};
			if (managedRankingStart) params.start = managedRankingStart;
			if (managedRankingEnd) params.end = managedRankingEnd;
			if (managedRankingModel) params.model = managedRankingModel;
			const data = await api.get<{ ranking: ManagedRankingRow[] }>(
				'/auth/managed/usage/ranking',
				params
			);
			managedRanking = data.ranking;
		});
	}

	async function addManagedProjectMember() {
		if (!managedProjectMemberForm.resource_id || !managedProjectMemberForm.subject_id) {
			fail('请选择项目和用户。');
			return;
		}
		await run(async () => {
			await api.post('/auth/managed/project-memberships', {
				resource_id: managedProjectMemberForm.resource_id,
				subject_id: managedProjectMemberForm.subject_id,
				role: managedProjectMemberForm.role || 'member'
			});
			await refreshManagedProjectMemberships();
		}, '成员已加入项目');
	}

	function removeManagedProjectMember(membership: ProjectMembership) {
		askConfirm('移除项目成员', `确认从项目中移除 ${membershipSubjectLabel(membership)}？`, '移除', async () => {
			await run(async () => {
				await api.delete(`/auth/managed/project-memberships/${membership.id}`);
				await refreshManagedProjectMemberships();
			}, '成员已移除');
		});
	}

	async function addManagedTeamMember() {
		if (!managedTeamMemberForm.resource_id || !managedTeamMemberForm.subject_id) {
			fail('请选择权限组和用户。');
			return;
		}
		await run(async () => {
			await api.post('/auth/managed/team-memberships', {
				resource_id: managedTeamMemberForm.resource_id,
				subject_id: managedTeamMemberForm.subject_id,
				role: managedTeamMemberForm.role || 'member'
			});
			await refreshManagedTeamMemberships();
		}, '成员已加入权限组');
	}

	async function setManagedTeamMemberState(membership: TeamMembership, state: ResourceState) {
		await run(async () => {
			await api.patch(`/auth/managed/team-memberships/${membership.id}`, { state });
			await refreshManagedTeamMemberships();
		});
	}

	function setUsageRange(days: number, key: string) {
		const range = usageRangeForDays(days);
		usageStart = range.start;
		usageEnd = range.end;
		usageRangeKey = key;
	}

	async function copyText(value: string, key: string) {
		if (navigator.clipboard?.writeText) {
			await navigator.clipboard.writeText(value);
		} else {
			const textarea = document.createElement('textarea');
			textarea.value = value;
			textarea.style.position = 'fixed';
			textarea.style.opacity = '0';
			document.body.appendChild(textarea);
			textarea.focus();
			textarea.select();
			document.execCommand('copy');
			textarea.remove();
		}
		copiedItem = key;
		setTimeout(() => {
			if (copiedItem === key) copiedItem = '';
		}, 1400);
	}

	async function run(fn: () => Promise<void>, success = '') {
		loading = true;
		pageError = '';
		try {
			await fn();
			if (success) toastSuccess(success);
		} catch (error) {
			pageError = errorMessage(error);
			toastError(pageError);
		} finally {
			loading = false;
		}
	}

	function emptyInventory(): Inventory {
		return {
			subjects: [],
			subjectsTotal: 0,
			subjectRateOverrides: {},
			projects: [],
			projectsTotal: 0,
			memberships: [],
			membershipsTotal: 0,
			keys: [],
			keysTotal: 0,
			models: [],
			modelsTotal: 0,
			entitlements: [],
			entitlementsTotal: 0,
			teams: [],
			teamsTotal: 0,
			teamMemberships: [],
			teamMembershipsTotal: 0,
			modelTeamGrants: [],
			modelTeamGrantsTotal: 0,
			teamTokenQuotas: [],
			upstreams: [],
			upstreamsTotal: 0,
			ratePolicies: [],
			ratePoliciesTotal: 0,
			usage: [],
			usageTotals: null,
			ranking: [],
			analyticsBuckets: [],
			analyticsDrilldown: [],
			audit: [],
			auditTotal: 0
		};
	}

	// 依赖 inventory 的 label 函数:委托给 admin-config 的纯函数版本,捕获闭包上下文
	function subjectLabel(id: string | null | undefined): string {
		return subjectLabelConfig(id, labelCtx);
	}
	function membershipSubjectLabel(membership: ProjectMembership | TeamMembership): string {
		return membershipSubjectLabelConfig(membership, labelCtx);
	}
	function projectLabel(id: string | null | undefined): string {
		return projectLabelConfig(id, labelCtx);
	}
	function keyLabel(id: string | null | undefined): string {
		return keyLabelConfig(id, labelCtx);
	}
	function modelLabel(id: string | null | undefined): string {
		return modelLabelConfig(id, labelCtx);
	}
	function teamLabel(id: string | null | undefined): string {
		return teamLabelConfig(id, labelCtx);
	}

	function scopeOptions(scope: string, subjectQuery = '') {
		if (scope === 'subject') {
			return subjectOptions(subjectQuery).map((item) => ({ id: item.id, label: subjectDisplay(item) }));
		}
		if (scope === 'project') return projectOptions.map((item) => ({ id: item.id, label: item.name }));
		return keyOptions.map((item) => ({ id: item.id, label: `${item.name} (${item.key_prefix})` }));
	}

	/** 可搜索用户下拉:选项来自 /admin/subjects/options,按查询词缓存(服务端分页改造)。 */
	function subjectOptions(query: string): SubjectOption[] {
		return subjectOptionsByQuery[query] ?? [];
	}

	function isModelUpstreamConflict(error: unknown): error is { detail: { code: string; upstream_count?: number } } {
		return Boolean(
			isApiError(error) &&
				error.status === 409 &&
				error.detail &&
				typeof error.detail === 'object' &&
				'code' in error.detail &&
				(error.detail as { code?: string }).code === 'model_alias_has_upstreams'
		);
	}
</script>
{#if !connected}
	<AuthScreen
		{ready}
		bind:loginForm
		bind:registerForm
		bind:rememberSession
		{pageError}
		{loading}
		onLogin={loginAccount}
		onRegister={registerAccount}
		onRefreshReady={refreshReady}
	/>
{:else}
	<div class="app">
		<aside class="sidebar">
			<div class="brand">
				<span class="brand-logo">
					<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#e7ecf5" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
						<path d="M8 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h3" />
						<path d="M16 4h3a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-3" />
						<path d="m9 12 3-3" />
						<path d="m12 9 3 3-3 3" />
					</svg>
				</span>
				<span class="brand-text">
					<strong>LLM Gateway</strong>
					<span class="env-chip {envClass}">{diagnostics?.environment ?? 'local'}</span>
				</span>
			</div>
			<div class="sidebar-nav">
				{#if !isAdmin}
					<nav class="nav-group" aria-label="账号">
						<div class="nav-group-title">账号</div>
						<button class="nav-button" class:active={active === 'usage'} type="button" onclick={() => (active = 'usage')}>
							<KeyRound size={16} /><span>我的访问权限</span>
						</button>
					</nav>
					<nav class="nav-group" aria-label="市场">
						<div class="nav-group-title">市场</div>
						<button class="nav-button" class:active={active === 'skill-market'} type="button" onclick={() => (active = 'skill-market')}>
							<Package size={16} /><span>Skill 市场</span>
						</button>
						<button class="nav-button" class:active={active === 'mcp-market'} type="button" onclick={() => (active = 'mcp-market')}>
							<Plug size={16} /><span>MCP 市场</span>
						</button>
					</nav>
				{:else}
					{#each navGroups as group}
						<nav class="nav-group" aria-label={group}>
							<div class="nav-group-title">{group}</div>
							{#each sections.filter((section) => section.group === group) as section}
								{@const Icon = section.icon}
								<button class="nav-button" class:active={active === section.id} type="button" onclick={() => (active = section.id)}>
									<Icon size={16} /><span>{section.label}</span>
								</button>
							{/each}
						</nav>
					{/each}
				{/if}
			</div>
			<div class="user-card">
				<span class="avatar">{(profile?.subject.name ?? '?').trim().charAt(0).toUpperCase() || '?'}</span>
				<div class="user-card-meta">
					<strong>{profile?.subject.name}</strong>
					<span>{profile?.subject.login_username ?? '—'}</span>
				</div>
				<button class="ghost icon-button sm" type="button" aria-label="切换主题" onclick={() => applyTheme(theme === 'dark' ? 'light' : 'dark')}>
					{#if theme === 'dark'}<Sun size={15} />{:else}<Moon size={15} />{/if}
				</button>
				<button class="ghost icon-button sm" type="button" aria-label="退出登录" onclick={disconnect}><LogOut size={15} /></button>
			</div>
		</aside>
		<main class="main">
			<div class="topbar">
				<div class="topbar-title">
					<strong>{topbarTitle}</strong>
					{#if topbarSubtitle}<span>{topbarSubtitle}</span>{/if}
				</div>
				<div class="topbar-status">
					<span class="status-dot" class:ok={ready?.checks.postgres}>Postgres</span>
					<span class="status-dot" class:ok={ready?.checks.redis}>Redis</span>
					<button class="ghost icon-button" type="button" aria-label="刷新" onclick={refreshAll} disabled={loading}><RefreshCw size={15} /></button>
				</div>
				{#if loading}<div class="topbar-progress"></div>{/if}
			</div>
			<section class="content">
				{#if !isAdmin && active !== 'skill-market' && active !== 'mcp-market'}
					<OwnedDashboard
						{profile}
						{ownUsage}
						{managedProjects}
						{managedTeams}
						{hasManagedResources}
						{managedUsage}
						{managedSubjectCandidates}
						{managedRoles}
						{managedProjectMemberships}
						{managedTeamMemberships}
						{membershipSubjectLabel}
						bind:ownUsageStart
						bind:ownUsageEnd
						bind:managedUsageScope
						bind:managedUsageResourceId
						bind:managedProjectMemberForm
						bind:managedTeamMemberForm
						bind:managedSubjectSearch
						bind:ownKeyForm
						bind:gatewayBaseUrl
						bind:ownPasswordForm
						{preferredModel}
						{visibleKeyHint}
						{gatewayV1Base}
						{responsesEndpoint}
						{messagesEndpoint}
						{gatewayOrigin}
						{codexEnvCommand}
						{codexConfigCommand}
						{claudeEnvCommand}
						{copiedItem}
						{loading}
						onRefreshOwnUsage={refreshOwnUsage}
						onRefreshManagedUsage={refreshManagedUsage}
						managedRanking={managedRanking}
						bind:managedRankingStart
						bind:managedRankingEnd
						bind:managedRankingModel
						bind:managedRankingLimit
						onRefreshManagedRanking={refreshManagedRanking}
						onRefreshManagedSubjects={refreshManagedSubjects}
						onRefreshManagedProjectMemberships={() => refreshManagedProjectMemberships()}
						onRefreshManagedTeamMemberships={() => refreshManagedTeamMemberships()}
						onAddManagedProjectMember={addManagedProjectMember}
						onRemoveManagedProjectMember={removeManagedProjectMember}
						onAddManagedTeamMember={addManagedTeamMember}
						onSetManagedTeamMemberState={setManagedTeamMemberState}
						onIssueOwnKey={issueOwnKey}
						onSetOwnKeyState={setOwnKeyState}
						onChangeOwnPassword={changeOwnPassword}
						onCopy={copyText}
					/>
				{:else if active === 'skill-market'}
					<SkillMarketSection client={api} teams={isAdmin ? teamOptions : marketTeams} />
				{:else if active === 'mcp-market'}
					<McpMarketSection client={api} teams={isAdmin ? teamOptions : marketTeams} />
				{:else if active === 'models'}
					<div class="toolbar">
						<span class="search-input">
							<Search size={14} />
							<input
								bind:value={modelSearch}
								placeholder="搜索别名或上游模型名，回车搜索"
								aria-label="搜索模型"
								onkeydown={(event) => {
									if (event.key === 'Enter') {
										modelPage = 1;
										void run(fetchModels);
									}
								}}
							/>
						</span>
						<button type="button" onclick={() => (modelDrawerOpen = true)}><Plus size={15} />创建模型别名</button>
					</div>
					<section class="panel flush">
						<div class="table-wrap">
							<table>
								<thead><tr><th>别名</th><th>上游格式</th><th>能力</th><th>粘性 TTL</th><th>IP 策略</th><th>状态</th><th></th></tr></thead>
								<tbody>
									{#each inventory.models as model}
										{@const fmt = deriveUpstreamFormat(model.litellm_model)}
										<tr onclick={() => (detail = { kind: 'model', id: model.id })}>
											<td><strong>{model.alias}</strong><br /><span class="sub">{model.upstream_model_name}</span></td>
											<td class="nowrap" onclick={(event) => event.stopPropagation()}>
												<select
													aria-label="切换上游格式"
													value={fmt}
													onchange={(event) => {
														const format = event.currentTarget.value as UpstreamFormat;
														const next = composeLitellmModel(format, model.upstream_model_name);
														if (next !== model.litellm_model) void patchModel(model.id, { litellm_model: next });
													}}
												>
													<option value="openai">{UPSTREAM_FORMAT_LABEL.openai}</option>
													<option value="openai_chat_completions">{UPSTREAM_FORMAT_LABEL.openai_chat_completions}</option>
													<option value="anthropic">{UPSTREAM_FORMAT_LABEL.anthropic}</option>
													<option value="hosted_vllm">{UPSTREAM_FORMAT_LABEL.hosted_vllm}</option>
												</select>
											</td>
											<td>
												<span class="cap-dots" title="Streaming / Tools / Reasoning">
													<span class={model.supports_streaming ? 'cap-on' : 'cap-off'}>S</span>
													<span class={model.supports_tools ? 'cap-on' : 'cap-off'}>T</span>
													<span class={model.supports_reasoning ? 'cap-on' : 'cap-off'}>R</span>
												</span>
											</td>
											<td class="mono nowrap">{model.sticky_ttl_seconds}s</td>
											<td><StateBadge value={model.ip_policy_mode} /></td>
											<td onclick={(event) => event.stopPropagation()}>
												<Switch checked={model.state === 'active'} label="切换模型状态" onToggle={() => void patchModel(model.id, { state: model.state === 'active' ? 'disabled' : 'active' })} />
											</td>
											<td class="nowrap" onclick={(event) => event.stopPropagation()}>
												<RowMenu
													label="模型操作"
													items={[
														{ label: '编辑 CIDR', onclick: () => editModelCidrs(model) },
														{ label: '编辑 TTL', onclick: () => (ttlEditor = { id: model.id, alias: model.alias, value: String(model.sticky_ttl_seconds) }) },
														{ label: '删除', danger: true, onclick: () => deleteModel(model) }
													]}
												/>
											</td>
										</tr>
									{:else}
										<tr><td colspan="7"><EmptyState title="没有匹配的模型别名" hint="换个搜索关键词，或创建第一个模型别名。" actionLabel="创建模型别名" onAction={() => (modelDrawerOpen = true)} /></td></tr>
									{/each}
								</tbody>
							</table>
						</div>
						<Pagination
							total={inventory.modelsTotal}
							page={modelPage}
							size={listPageSize}
							onPage={(page) => { modelPage = page; void run(fetchModels); }}
							sizes={[20, 50, 100]}
							onSizeChange={(size) => { listPageSize = size; modelPage = 1; void run(fetchModels); }}
						/>
					</section>
				{:else if active === 'upstreams'}
					<div class="toolbar">
						<span class="search-input">
							<Search size={14} />
							<input
								bind:value={upstreamSearch}
								placeholder="搜索名称或 Base URL，回车搜索"
								aria-label="搜索上游"
								onkeydown={(event) => {
									if (event.key === 'Enter') {
										upstreamPage = 1;
										void run(fetchUpstreams);
									}
								}}
							/>
						</span>
						<button type="button" onclick={() => (upstreamDrawerOpen = true)}><Plus size={15} />创建上游</button>
					</div>
					<UpstreamTable rows={inventory.upstreams} healthResults={healthResults} modelLabel={modelLabel} onCheck={checkUpstream} onState={setUpstreamState} onPatch={patchUpstream} onDelete={deleteUpstream} onError={fail} />
					<Pagination
						total={inventory.upstreamsTotal}
						page={upstreamPage}
						size={listPageSize}
						onPage={(page) => { upstreamPage = page; void run(fetchUpstreams); }}
						sizes={[20, 50, 100]}
						onSizeChange={(size) => { listPageSize = size; upstreamPage = 1; void run(fetchUpstreams); }}
					/>
				{:else if active === 'subjects'}
					<div class="toolbar">
						<span class="search-input">
							<Search size={14} />
							<input
								bind:value={subjectSearch}
								placeholder="搜索姓名、工号或备注，回车搜索"
								aria-label="搜索用户"
								onkeydown={(event) => {
									if (event.key === 'Enter') {
										subjectPage = 1;
										void run(fetchSubjects);
									}
								}}
							/>
						</span>
						<button type="button" onclick={() => (subjectDrawerOpen = true)}><Plus size={15} />创建用户</button>
					</div>
					<section class="panel flush">
						<div class="table-wrap">
							<table>
								<thead><tr><th>姓名</th><th>类型</th><th>状态</th><th>限流</th><th>备注</th><th></th></tr></thead>
								<tbody>
									{#each inventory.subjects as subject}
										{@const ov = inventory.subjectRateOverrides[subject.id]}
										<tr onclick={() => (detail = { kind: 'subject', id: subject.id })}>
											<td><strong>{subject.name}</strong><br /><span class="sub mono">{subject.login_username ?? short(subject.id)}</span></td>
											<td>{subjectTypeLabel(subject.type)}</td>
											<td onclick={(event) => event.stopPropagation()}>
												<Switch checked={subject.state === 'active'} label="切换用户状态" onToggle={() => void setSubjectState(subject.id, subject.state === 'active' ? 'disabled' : 'active')} />
											</td>
											<td class="nowrap">
												{#if ov && (ov.rpm !== null || ov.concurrency !== null)}
													<span class="rate-override"><span title="并发上限">C <strong>{ov.concurrency ?? '继承'}</strong></span><span title="每分钟请求数上限">R <strong>{ov.rpm ?? '继承'}</strong></span></span>
												{:else}<span class="muted">继承</span>{/if}
											</td>
											<td class="ellipsis">{subject.notes}</td>
											<td class="nowrap" onclick={(event) => event.stopPropagation()}>
												<RowMenu
													label="用户操作"
													items={[
														{ label: '编辑姓名', onclick: () => (textEditor = { title: '编辑真实姓名', label: '真实姓名', value: subject.name, onSave: (value) => void patchSubject(subject.id, { name: value }) }) },
														{ label: '编辑备注', onclick: () => (textEditor = { title: '编辑备注', label: '备注', value: subject.notes ?? '', multiline: true, onSave: (value) => void patchSubject(subject.id, { notes: value }) }) },
														{ label: '编辑限流', onclick: () => openRateEditor(subject) },
														...(ov && (ov.rpm !== null || ov.concurrency !== null) ? [{ label: '清除限流', onclick: () => void clearSubjectRateOverride(subject.id) }] : []),
														{ label: '重置密码', onclick: () => (passwordReset = { id: subject.id, name: subjectDisplay(subject), password: '' }) },
														{ label: '删除', danger: true, onclick: () => deleteSubject(subject) }
													]}
												/>
											</td>
										</tr>
									{:else}
										<tr><td colspan="6"><EmptyState title="没有匹配的用户" hint="换个搜索关键词，或创建新用户。" /></td></tr>
									{/each}
								</tbody>
							</table>
						</div>
						<Pagination
							total={inventory.subjectsTotal}
							page={subjectPage}
							size={listPageSize}
							onPage={(page) => { subjectPage = page; void run(fetchSubjects); }}
							sizes={[20, 50, 100]}
							onSizeChange={(size) => { listPageSize = size; subjectPage = 1; void run(fetchSubjects); }}
						/>
					</section>
				{:else if active === 'keys'}
					<div class="toolbar">
						<span class="search-input">
							<Search size={14} />
							<input
								bind:value={keyListSubjectSearch}
								placeholder="姓名、工号、密钥名或前缀，回车搜索"
								aria-label="搜索密钥"
								onkeydown={(event) => {
									if (event.key === 'Enter') {
										keyPage = 1;
										void run(fetchKeys);
									}
								}}
							/>
						</span>
						<div class="actions">
							<select
								aria-label="项目筛选"
								value={keyProjectFilter}
								onchange={() => { keyPage = 1; void run(fetchKeys); }}
							>
								<option value="">全部项目</option>
								{#each projectOptions as project}<option value={project.id}>{project.name}</option>{/each}
							</select>
							<select
								aria-label="状态筛选"
								value={keyStateFilter}
								onchange={() => { keyPage = 1; void run(fetchKeys); }}
							>
								<option value="">全部状态</option>
								<option value="active">启用</option>
								<option value="disabled">停用</option>
							</select>
							<button type="button" onclick={() => (keyDrawerOpen = true)}><Plus size={15} />签发密钥</button>
						</div>
					</div>
					<section class="panel flush">
						<div class="table-wrap">
							<table>
								<thead><tr><th>名称</th><th>前缀</th><th>用户</th><th>项目</th><th>状态</th></tr></thead>
								<tbody>
									{#each inventory.keys as key}
										<tr onclick={() => (detail = { kind: 'key', id: key.id })}>
											<td><strong>{key.name}</strong></td>
											<td><code>{key.key_prefix}</code></td>
											<td>{subjectLabel(key.subject_id)}</td>
											<td>{projectLabel(key.project_id)}</td>
											<td onclick={(event) => event.stopPropagation()}>
												<Switch checked={key.state === 'active'} label="切换密钥状态" onToggle={() => void setKeyState(key.id, key.state === 'active' ? 'disabled' : 'active')} />
											</td>
										</tr>
										{:else}
											<tr><td colspan="5"><EmptyState title="没有匹配的密钥" hint="调整筛选条件，或签发新密钥。" /></td></tr>
										{/each}
									</tbody>
								</table>
							</div>
							<Pagination
								total={inventory.keysTotal}
								page={keyPage}
								size={listPageSize}
								onPage={(page) => { keyPage = page; void run(fetchKeys); }}
								sizes={[20, 50, 100]}
								onSizeChange={(size) => { listPageSize = size; keyPage = 1; void run(fetchKeys); }}
							/>
						</section>
				{:else if active === 'teams'}
					<div class="toolbar">
						<span class="search-input">
							<Search size={14} />
							<input
								bind:value={teamSearch}
								placeholder="搜索权限组名称，回车搜索"
								aria-label="搜索权限组"
								onkeydown={(event) => {
									if (event.key === 'Enter') {
										teamPage = 1;
										void run(fetchTeams);
									}
								}}
							/>
						</span>
						<button type="button" onclick={() => (teamDrawerOpen = true)}><Plus size={15} />创建权限组</button>
					</div>
					<section class="panel flush">
						<div class="table-wrap">
							<table>
								<thead><tr><th>名称</th><th>内置</th><th>Token 配额</th><th>备注</th><th>状态</th></tr></thead>
								<tbody>
									{#each inventory.teams as team}
										<tr onclick={() => openTeamDrawer(team)}>
											<td><strong>{team.name}</strong><br /><span class="sub mono">{short(team.id)}</span></td>
											<td><StateBadge value={team.is_builtin} tone="accent" /></td>
											<td><QuotaChips row={teamQuotaRow(team.id)} /></td>
											<td class="ellipsis">{team.notes}</td>
											<td onclick={(event) => event.stopPropagation()}>
												<Switch checked={team.state === 'active'} label="切换权限组状态" onToggle={() => void patchTeam(team.id, { state: team.state === 'active' ? 'disabled' : 'active' })} />
											</td>
										</tr>
										{:else}
											<tr><td colspan="5"><EmptyState title="没有匹配的权限组" hint="换个搜索关键词，或创建新权限组。" actionLabel="创建权限组" onAction={() => (teamDrawerOpen = true)} /></td></tr>
										{/each}
									</tbody>
								</table>
							</div>
							<Pagination
								total={inventory.teamsTotal}
								page={teamPage}
								size={listPageSize}
								onPage={(page) => { teamPage = page; void run(fetchTeams); }}
								sizes={[20, 50, 100]}
								onSizeChange={(size) => { listPageSize = size; teamPage = 1; void run(fetchTeams); }}
							/>
						</section>
						<section class="panel">
							<div class="section-head"><h2>成员关系</h2></div>
							<div class="list-toolbar">
								<span class="search-input">
									<Search size={14} />
									<input
										bind:value={teamMembershipSubjectSearch}
										placeholder="姓名或工号，回车搜索"
										aria-label="搜索成员"
										onkeydown={(event) => {
											if (event.key === 'Enter') {
												teamMembershipPage = 1;
												void run(fetchTeamMemberships);
											}
										}}
									/>
								</span>
								<div class="actions">
									<select
										aria-label="权限组筛选"
										value={teamMembershipTeamFilter}
										onchange={() => { teamMembershipPage = 1; void run(fetchTeamMemberships); }}
									>
										<option value="">全部权限组</option>
										{#each teamOptions as team}<option value={team.id}>{team.name}</option>{/each}
									</select>
									<input
										bind:value={teamMembershipRoleFilter}
										placeholder="角色(精确)"
										aria-label="角色筛选"
										onkeydown={(event) => {
											if (event.key === 'Enter') {
												teamMembershipPage = 1;
												void run(fetchTeamMemberships);
											}
										}}
									/>
									<select
										aria-label="状态筛选"
										value={teamMembershipStateFilter}
										onchange={() => { teamMembershipPage = 1; void run(fetchTeamMemberships); }}
									>
										<option value="">全部状态</option>
										<option value="active">启用</option>
										<option value="disabled">停用</option>
									</select>
								</div>
							</div>
							<div class="table-wrap">
								<table>
									<thead><tr><th>权限组</th><th>用户</th><th>角色</th><th>状态</th></tr></thead>
									<tbody>
										{#each inventory.teamMemberships as membership}
											<tr>
												<td>{membership.team_name ?? teamLabel(membership.team_id)}</td>
												<td>{membershipSubjectLabel(membership)}</td>
												<td>{membership.role}</td>
												<td><Switch checked={membership.state === 'active'} label="切换成员状态" onToggle={() => void setTeamMembershipState(membership.id, membership.state === 'active' ? 'disabled' : 'active')} /></td>
											</tr>
										{:else}
											<tr><td colspan="4"><EmptyState title="没有匹配的成员关系" hint="在权限组抽屉里添加成员。" /></td></tr>
										{/each}
									</tbody>
								</table>
							</div>
							<Pagination
								total={inventory.teamMembershipsTotal}
								page={teamMembershipPage}
								size={listPageSize}
								onPage={(page) => { teamMembershipPage = page; void run(fetchTeamMemberships); }}
								sizes={[20, 50, 100]}
								onSizeChange={(size) => { listPageSize = size; teamMembershipPage = 1; void run(fetchTeamMemberships); }}
							/>
						</section>
						<section class="panel">
							<div class="section-head">
								<h2>模型授权</h2>
								<select
									aria-label="按权限组筛选授权"
									value={grantTeamFilter}
									onchange={() => { grantPage = 1; void run(fetchModelTeamGrants); }}
								>
									<option value="">全部权限组</option>
									{#each teamOptions as team}<option value={team.id}>{team.name}</option>{/each}
								</select>
							</div>
							<div class="table-wrap">
								<table>
									<thead><tr><th>模型</th><th>权限组</th><th>状态</th></tr></thead>
									<tbody>
										{#each inventory.modelTeamGrants as grant}
											<tr>
												<td>{grant.model_alias ?? modelLabel(grant.model_alias_id)}</td>
												<td>{grant.team_name ?? teamLabel(grant.team_id)}</td>
												<td><Switch checked={grant.state === 'active'} label="切换授权状态" onToggle={() => void setModelTeamGrantState(grant.id, grant.state === 'active' ? 'disabled' : 'active')} /></td>
											</tr>
										{:else}
											<tr><td colspan="3" class="empty">暂无模型授权，在权限组抽屉的「模型授权」页签添加。</td></tr>
										{/each}
									</tbody>
								</table>
							</div>
							<Pagination
								total={inventory.modelTeamGrantsTotal}
								page={grantPage}
								size={listPageSize}
								onPage={(page) => { grantPage = page; void run(fetchModelTeamGrants); }}
							/>
</section>
				{:else if active === 'rate'}
					<div class="toolbar">
						<select
							aria-label="按范围筛选策略"
							value={rateScopeFilter}
							onchange={() => { ratePage = 1; void run(fetchRatePolicies); }}
						>
							<option value="">全部范围</option>
							<option value="subject">用户</option>
							<option value="project">项目</option>
							<option value="key">密钥</option>
						</select>
						<button type="button" onclick={() => { rateDrawerOpen = true; void fetchKeyOptions().catch(() => undefined); }}><Plus size={15} />创建限流策略</button>
					</div>
					<section class="panel flush">
						<div class="table-wrap">
							<table>
								<thead><tr><th>范围</th><th>对象</th><th>RPM</th><th>并发</th><th>状态</th></tr></thead>
								<tbody>
									{#each inventory.ratePolicies as policy}
										<tr>
											<td>{scopeLabel(policy.scope)}</td>
											<td>{policy.scope_name ?? (policy.scope === 'subject' ? subjectLabel(policy.scope_id) : policy.scope === 'project' ? projectLabel(policy.scope_id) : keyLabel(policy.scope_id))}</td>
											<td class="mono">{policy.requests_per_minute ?? '继承'}</td>
											<td class="mono">{policy.concurrency_limit ?? '继承'}</td>
											<td><Switch checked={policy.state === 'active'} label="切换策略状态" onToggle={() => void setRateState(policy.id, policy.state === 'active' ? 'disabled' : 'active')} /></td>
										</tr>
									{:else}
										<tr><td colspan="5"><EmptyState title="暂无限流策略" hint="创建策略来限制每分钟请求数或并发。" actionLabel="创建策略" onAction={() => (rateDrawerOpen = true)} /></td></tr>
									{/each}
								</tbody>
							</table>
						</div>
						<Pagination
							total={inventory.ratePoliciesTotal}
							page={ratePage}
							size={listPageSize}
							onPage={(page) => { ratePage = page; void run(fetchRatePolicies); }}
							sizes={[20, 50, 100]}
							onSizeChange={(size) => { listPageSize = size; ratePage = 1; void run(fetchRatePolicies); }}
						/>
					</section>
				{:else if active === 'usage'}
					<section class="ribbon">
						<div class="ribbon-head">
							<h2>流量带</h2>
							<div class="ribbon-legend"><span><i></i>成功</span><span><i class="fail"></i>失败</span></div>
							<div class="actions">
								<Segmented
									options={[{ value: '1h', label: '1 小时' }, { value: '1d', label: '1 天' }, { value: '1w', label: '1 周' }, { value: '1m', label: '1 月' }]}
									selected={usageRangeKey}
									onSelect={(value) => {
										if (value === '1h') setUsageRange(1 / 24, value);
										else if (value === '1d') setUsageRange(1, value);
										else if (value === '1w') setUsageRange(7, value);
										else setUsageRange(30, value);
										void refreshUsageAnalytics();
									}}
								/>
								<div class="popover-anchor">
									<button class="secondary" type="button" onclick={() => (usageFilterOpen = !usageFilterOpen)} aria-expanded={usageFilterOpen}><SlidersHorizontal size={14} />筛选</button>
									{#if usageFilterOpen}
										<div class="popover">
											<div class="form-grid">
												<label>开始时间<input type="datetime-local" bind:value={usageStart} /></label>
												<label>结束时间<input type="datetime-local" bind:value={usageEnd} /></label>
												<label>时间粒度<select bind:value={analyticsBucket}><option value="minute">分钟</option><option value="hour">小时</option><option value="day">天</option></select></label>
												<label>分析维度<select bind:value={analyticsDimension}><option value="model">模型</option><option value="subject">用户</option><option value="project">项目</option><option value="endpoint">协议</option><option value="outcome">结果</option><option value="streaming">流式</option></select></label>
												<label>模型筛选<select bind:value={modelFilter}><option value="">全部</option>{#each modelOptions as model}<option value={model.alias}>{model.alias}</option>{/each}</select></label>
												<label>项目筛选<select bind:value={projectFilter}><option value="">全部</option>{#each projectOptions as project}<option value={project.id}>{project.name}</option>{/each}</select></label>
												<label>搜索用户<input bind:value={usageSubjectSearch} placeholder="输入姓名或工号" oninput={() => queueSubjectOptions(usageSubjectSearch)} /></label>
												<label>用户筛选<select bind:value={subjectFilter}><option value="">全部</option>{#each subjectOptions(usageSubjectSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label>
											</div>
											<div class="actions" style="justify-content: flex-end;">
												<button class="secondary" type="button" onclick={() => (usageFilterOpen = false)}>取消</button>
												<button type="button" onclick={refreshUsageAnalytics} disabled={loading}>{loading ? '查询中' : '查询'}</button>
											</div>
										</div>
									{/if}
								</div>
							</div>
						</div>
						<TrafficRibbon rows={inventory.analyticsBuckets} />
					</section>
					<div class="kpi-row">
						<div class="kpi-card">
							<div class="kpi-head"><span class="kpi-label">请求数</span></div>
							<div class="kpi-body"><span class="kpi-value">{fmtNumber(totals.requests)}</span><span class="kpi-spark"><Sparkline values={sparkRequests} /></span></div>
							<div class="kpi-foot">成功 {fmtNumber(totals.success)} · 失败 {fmtNumber(totals.failure)}</div>
						</div>
						<div class="kpi-card">
							<div class="kpi-head"><span class="kpi-label">总 Token</span></div>
							<div class="kpi-body"><span class="kpi-value flow">{fmtNumber(totals.total)}</span><span class="kpi-spark"><Sparkline values={sparkTokens} /></span></div>
							<div class="kpi-foot">输入 {fmtNumber(totals.prompt)} · 输出 {fmtNumber(totals.completion)}</div>
						</div>
						<div class="kpi-card">
							<div class="kpi-head"><span class="kpi-label">成功率</span></div>
							<div class="kpi-body"><span class="kpi-value">{fmtPercent(successRatio)}</span><span class="kpi-spark"><Sparkline values={sparkSuccess} /></span></div>
							<div class="kpi-foot">Retry {fmtNumber(analyticsPerformance.retry)} · Fallback {fmtNumber(analyticsPerformance.fallback)}</div>
						</div>
						<div class="kpi-card">
							<div class="kpi-head"><span class="kpi-label">平均延迟</span></div>
							<div class="kpi-body"><span class="kpi-value">{msLabel(analyticsPerformance.latencyWeight ? analyticsPerformance.latencyTotal / analyticsPerformance.latencyWeight : null)}</span><span class="kpi-spark"><Sparkline values={sparkLatency} /></span></div>
							<div class="kpi-foot">TTFT {msLabel(analyticsPerformance.ttftWeight ? analyticsPerformance.ttftTotal / analyticsPerformance.ttftWeight : null)} · vLLM 覆盖 {fmtNumber(analyticsPerformance.vllmObserved)}/{fmtNumber(analyticsPerformance.requests)}</div>
						</div>
					</div>
					<div class="chip-row">
						<span class="chip">用户 <strong>{fmtNumber(inventory.subjectsTotal)}</strong></span>
						<span class="chip">项目 <strong>{fmtNumber(inventory.projectsTotal)}</strong></span>
						<span class="chip">密钥 <strong>{fmtNumber(inventory.keysTotal)}</strong></span>
						<span class="chip">模型 <strong>{fmtNumber(inventory.modelsTotal)}</strong></span>
					</div>
					<section class="panel">
						<div class="section-head">
							<div>
								<h2>实时负载</h2>
								<p>每个上游副本的 vLLM 压力来自对应 <code>/metrics</code>；多个浏览器共享 Redis 缓存。</p>
							</div>
							<div class="actions">
								<StateBadge value={realtimeStatus} tone={realtimeStatus === '已连接' ? 'success' : 'neutral'} />
								<button class={realtimeLocked ? '' : 'secondary'} type="button" onclick={() => (realtimeLocked = !realtimeLocked)}>{realtimeLocked ? '解锁排序' : '锁定顺序·显示全部'}</button>
								<button class="secondary" type="button" onclick={startRealtimeStream}>重连</button>
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
											<td>{upstream.upstream_name}<br /><span class="sub mono">{short(upstream.upstream_id)}</span></td>
											<td>{upstream.model_alias || '未知'}</td>
											<td>{metricsKindLabel(upstream.vllm?.kind)}</td>
											<td class="mono">{upstream.vllm?.tokens_per_second == null ? '等待样本' : tokenRateLabel(upstream.vllm.tokens_per_second)}</td>
											<td class="mono">{upstream.active_connections}</td>
											<td class="mono">{upstream.vllm?.running ?? '无'} / {upstream.vllm?.waiting ?? '无'}</td>
											<td class="mono">{upstream.vllm?.router?.running_requests ?? upstream.vllm?.router?.worker_load ?? '无'} / {upstream.vllm?.router?.active_workers ?? '无'}</td>
											<td class="mono">{ratioLabel(upstream.vllm?.kv_cache_usage)} / {ratioLabel(upstream.vllm?.prefix_cache_hit_ratio)}</td>
											<td>{upstream.vllm?.ok ? '正常' : upstream.vllm?.error ?? '未抓取'}<br /><span class="sub">{upstream.vllm?.metrics_url ?? ''}</span></td>
										</tr>
									{:else}
										<tr><td colspan="9" class="empty">暂无实时负载数据。</td></tr>
									{/each}
								</tbody>
							</table>
						</div>
					</section>
					<section class="panel">
						<div class="section-head"><h2>最近时间桶</h2></div>
						<AnalyticsBucketTable rows={visibleAnalyticsBuckets} maxTokens={analyticsMaxTokens} />
					</section>
					<section class="panel">
						<div class="section-head"><h2>Top Drilldown</h2></div>
						<AnalyticsDrilldownTable rows={visibleAnalyticsDrilldown} />
					</section>
					<section class="panel">
						<div class="section-head"><h2>Top 汇总明细</h2></div>
						<UsageTable rows={visibleUsageRows} subjectLabel={subjectLabel} projectLabel={projectLabel} />
					</section>
				{:else if active === 'ranking'}
					<div class="toolbar">
						<Segmented
							options={[{ value: '1h', label: '1 小时' }, { value: '1d', label: '1 天' }, { value: '1w', label: '1 周' }, { value: '1m', label: '1 月' }]}
							selected={usageRangeKey}
							onSelect={(value) => {
								if (value === '1h') setUsageRange(1 / 24, value);
								else if (value === '1d') setUsageRange(1, value);
								else if (value === '1w') setUsageRange(7, value);
								else setUsageRange(30, value);
							}}
						/>
						<div class="actions">
							<input type="datetime-local" bind:value={usageStart} aria-label="开始时间" />
							<input type="datetime-local" bind:value={usageEnd} aria-label="结束时间" />
							<select bind:value={rankingModel} aria-label="模型筛选"><option value="">全部模型</option>{#each modelOptions as model}<option value={model.alias}>{model.alias}</option>{/each}</select>
							<input type="number" bind:value={rankingLimit} min="1" max="100" aria-label="Top N" title="Top N" />
							<button type="button" onclick={refreshUsageRanking} disabled={loading}>{loading ? '查询中' : '查询'}</button>
						</div>
					</div>
					<section class="panel flush">
						<div class="table-wrap">
							<table>
								<thead><tr><th>#</th><th>用户 / Subject</th><th>请求数</th><th>输入 token</th><th>输出 token</th><th>总 token</th></tr></thead>
								<tbody>
									{#each rankingPageRows as row, index}
										<tr>
											<td class="mono">{(rankingPage - 1) * PAGE_SIZE.ranking + index + 1}</td>
											<td>{row.subject_name} / {row.login_username ?? row.subject_id}</td>
											<td class="mono">{fmtNumber(row.request_count)}</td>
											<td class="mono">{fmtNumber(row.prompt_tokens)}</td>
											<td class="mono">{fmtNumber(row.completion_tokens)}</td>
											<td class="mono"><strong>{fmtNumber(row.total_tokens)}</strong></td>
										</tr>
									{:else}
										<tr><td colspan="6"><EmptyState title="暂无用量数据" hint="调整时间范围或模型筛选后重新查询。" /></td></tr>
									{/each}
								</tbody>
							</table>
						</div>
						<Pagination total={rankingRows.length} page={rankingPage} size={PAGE_SIZE.ranking} onPage={(page) => (rankingPage = page)} />
					</section>
				{:else if active === 'audit'}
					<section class="panel">
						<AuditTable rows={inventory.audit} onDetail={(event) => (auditDetail = event)} />
						<Pagination
							total={inventory.auditTotal}
							page={auditPage}
							size={PAGE_SIZE.audit}
							onPage={(page) => { auditPage = page; void run(fetchAudit); }}
						/>
					</section>
				{:else if active === 'diagnostics'}
					<div class="grid">
						<div class="metric"><span>Postgres</span><strong>{ready?.checks.postgres ? '正常' : '异常'}</strong></div>
						<div class="metric"><span>Redis</span><strong>{ready?.checks.redis ? '正常' : '异常'}</strong></div>
						<div class="metric"><span>环境</span><strong>{diagnostics?.environment ?? '未知'}</strong></div>
						<div class="metric"><span>LiteLLM</span><strong>{diagnostics?.litellm_version ?? '未知'}</strong></div>
					</div>
					<section class="panel">
						<div class="section-head"><h2>健康巡检</h2></div>
						<p class="muted">自动探测每个活跃上游的 <code>/models</code>，故障时在 Redis 标记 UNHEALTHY 并从路由排除。关闭后 sidecar 仍运行但跳过探测，已有标记靠 TTL 自动过期恢复。</p>
						<div class="switch-row">
							<strong>自动巡检</strong>
							{#if healthCheckConfig}
								<Switch checked={healthCheckConfig.enabled} disabled={healthCheckToggling} label="切换自动巡检" onToggle={toggleHealthCheck} />
								<span class="muted">来源：{healthCheckConfig.source === 'redis_override' ? '运行时覆盖' : '环境变量默认'}</span>
							{:else}
								<span class="muted">配置加载中…</span>
							{/if}
						</div>
					</section>
					<UpstreamTable rows={inventory.upstreams} healthResults={healthResults} modelLabel={modelLabel} onCheck={checkUpstream} onState={setUpstreamState} onPatch={patchUpstream} onDelete={deleteUpstream} onError={fail} />
				{/if}
			</section>
		</main>
	</div>
{/if}
<Drawer open={subjectDrawerOpen} title="创建用户" subtitle="人类用户或服务账号，工号可选" onClose={() => (subjectDrawerOpen = false)}>
	<div class="drawer-form">
		<label>真实姓名<input bind:value={subjectForm.name} placeholder="张三" /></label>
		<label>工号<input bind:value={subjectForm.login_username} placeholder="l00014624（可选）" /></label>
		<label>初始密码<input type="password" bind:value={subjectForm.password} /></label>
		<label>类型<select bind:value={subjectForm.type}><option value="user">用户</option><option value="service">服务账号</option></select></label>
		<label class="span-2">备注<input bind:value={subjectForm.notes} /></label>
	</div>
	{#snippet footer()}
		<button class="secondary" type="button" onclick={() => (subjectDrawerOpen = false)}>取消</button>
		<button type="button" onclick={createSubject} disabled={loading}>创建用户</button>
	{/snippet}
</Drawer>

	<Drawer open={projectDrawerOpen} title="创建项目" subtitle="用量归因和项目成员关系" onClose={() => (projectDrawerOpen = false)}>
		<div class="drawer-form">
			<label>名称<input bind:value={projectForm.name} /></label>
			<label>搜索负责人<input bind:value={projectOwnerSearch} placeholder="输入姓名或工号" oninput={() => queueSubjectOptions(projectOwnerSearch)} /></label>
			<label>负责人<select bind:value={projectForm.owner_subject_id}><option value="">无</option>{#each subjectOptions(projectOwnerSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label>
			<label>备注<input bind:value={projectForm.notes} /></label>
		</div>
		{#snippet footer()}
			<button class="secondary" type="button" onclick={() => (projectDrawerOpen = false)}>取消</button>
			<button type="button" onclick={createProject} disabled={loading}>创建项目</button>
		{/snippet}
	</Drawer>

	<Drawer open={projectMemberDrawerOpen} title="添加项目成员" onClose={() => (projectMemberDrawerOpen = false)}>
		<div class="drawer-form">
			<label>项目<select bind:value={membershipForm.project_id}><option value="">项目</option>{#each projectOptions as project}<option value={project.id}>{project.name}</option>{/each}</select></label>
			<label>搜索用户<input bind:value={projectMemberSearch} placeholder="输入姓名或工号" oninput={() => queueSubjectOptions(projectMemberSearch)} /></label>
			<label>用户<select bind:value={membershipForm.subject_id}><option value="">用户</option>{#each subjectOptions(projectMemberSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label>
			<label>角色<select bind:value={membershipForm.role}>{#each managedRoles as role}<option value={role.value}>{role.label}</option>{/each}</select></label>
		</div>
		{#snippet footer()}
			<button class="secondary" type="button" onclick={() => (projectMemberDrawerOpen = false)}>取消</button>
			<button type="button" onclick={createMembership} disabled={loading}>添加成员</button>
		{/snippet}
	</Drawer>

	<Drawer open={keyDrawerOpen} title="签发密钥" subtitle="明文只在签发时展示一次" onClose={() => (keyDrawerOpen = false)}>
		<div class="drawer-form">
			<label>搜索用户<input bind:value={keySubjectSearch} placeholder="输入姓名或工号" oninput={() => queueSubjectOptions(keySubjectSearch)} /></label>
			<label>用户<select bind:value={keyForm.subject_id}><option value="">用户</option>{#each subjectOptions(keySubjectSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label>
			<label>项目<select bind:value={keyForm.project_id}><option value="">项目</option>{#each projectOptions as project}<option value={project.id}>{project.name}</option>{/each}</select></label>
			<label>名称<input bind:value={keyForm.name} /></label>
		</div>
		{#snippet footer()}
			<button class="secondary" type="button" onclick={() => (keyDrawerOpen = false)}>取消</button>
			<button type="button" onclick={issueKey} disabled={loading}>签发密钥</button>
		{/snippet}
	</Drawer>

	<Drawer open={upstreamDrawerOpen} title="创建上游" subtitle="模型别名背后的同构副本端点" wide onClose={() => (upstreamDrawerOpen = false)}>
		<div class="drawer-form">
			<label>模型<select bind:value={upstreamForm.model_alias_id}><option value="">选择模型</option>{#each modelOptions as model}<option value={model.id}>{model.alias}</option>{/each}</select></label>
		<label>名称<input bind:value={upstreamForm.name} /></label>
		<label class="span-2">Base URL<input bind:value={upstreamForm.base_url} placeholder="http://host:9000/v1" /></label>
		<label class="span-2">Metrics URL<input bind:value={upstreamForm.metrics_url} placeholder="可选，例如 http://router-host:29000/metrics" /></label>
		<label>健康检查路径<input bind:value={upstreamForm.health_path} /></label>
		<label>API key 引用<input bind:value={upstreamForm.api_key_ref} /></label>
		<label>API key 明文<input type="password" bind:value={upstreamForm.api_key_value} /></label>
		<label class="span-2">额外请求头<textarea bind:value={upstreamForm.extra_headers} placeholder="JSON 对象，例如 X-Auth: token"></textarea></label>
	</div>
	{#snippet footer()}
		<button class="secondary" type="button" onclick={() => (upstreamDrawerOpen = false)}>取消</button>
		<button type="button" onclick={createUpstream} disabled={loading}>创建上游</button>
	{/snippet}
</Drawer>

<Drawer open={teamDrawerOpen} title="创建权限组" onClose={() => (teamDrawerOpen = false)}>
	<div class="drawer-form">
		<label>名称<input bind:value={teamForm.name} /></label>
		<label>备注<input bind:value={teamForm.notes} /></label>
	</div>
	{#snippet footer()}
		<button class="secondary" type="button" onclick={() => (teamDrawerOpen = false)}>取消</button>
		<button type="button" onclick={createTeam} disabled={loading}>创建权限组</button>
	{/snippet}
</Drawer>

	<Drawer open={entitlementDrawerOpen} title="创建授权" subtitle="旧式授权：项目/用户/密钥级别" onClose={() => (entitlementDrawerOpen = false)}>
		<div class="drawer-form">
			<label>模型<select bind:value={entitlementForm.model_alias_id}><option value="">模型</option>{#each modelOptions as model}<option value={model.id}>{model.alias}</option>{/each}</select></label>
			<label>范围<select bind:value={entitlementForm.scope} onchange={() => { entitlementForm.scope_id = ''; if (entitlementForm.scope === 'key') void fetchKeyOptions().catch(() => undefined); }}><option value="project">项目</option><option value="subject">用户</option><option value="key">密钥</option></select></label>
			{#if entitlementForm.scope === 'subject'}
				<label>搜索用户<input bind:value={entitlementSubjectSearch} placeholder="输入姓名或工号" oninput={() => queueSubjectOptions(entitlementSubjectSearch)} /></label>
			{/if}
			<label>授权对象<select bind:value={entitlementForm.scope_id}><option value="">对象</option>{#each scopeOptions(entitlementForm.scope, entitlementSubjectSearch) as option}<option value={option.id}>{option.label}</option>{/each}</select></label>
		</div>
		{#snippet footer()}
			<button class="secondary" type="button" onclick={() => (entitlementDrawerOpen = false)}>取消</button>
			<button type="button" onclick={createEntitlement} disabled={loading}>授权访问</button>
		{/snippet}
	</Drawer>

	<Drawer open={rateDrawerOpen} title="创建限流策略" subtitle="生效限制取各层最小启用策略" onClose={() => (rateDrawerOpen = false)}>
		<div class="drawer-form">
			<label>范围<select bind:value={rateForm.scope} onchange={() => { rateForm.scope_id = ''; if (rateForm.scope === 'key') void fetchKeyOptions().catch(() => undefined); }}><option value="key">密钥</option><option value="subject">用户</option><option value="project">项目</option></select></label>
			{#if rateForm.scope === 'subject'}
				<label>搜索用户<input bind:value={rateSubjectSearch} placeholder="输入姓名或工号" oninput={() => queueSubjectOptions(rateSubjectSearch)} /></label>
			{/if}
			<label>对象<select bind:value={rateForm.scope_id}><option value="">对象</option>{#each scopeOptions(rateForm.scope, rateSubjectSearch) as option}<option value={option.id}>{option.label}</option>{/each}</select></label>
			<label>每分钟请求数<input type="number" min="0" bind:value={rateForm.requests_per_minute} placeholder="留空继承" /></label>
			<label>并发限制<input type="number" min="0" bind:value={rateForm.concurrency_limit} placeholder="留空继承" /></label>
		</div>
		{#snippet footer()}
			<button class="secondary" type="button" onclick={() => (rateDrawerOpen = false)}>取消</button>
			<button type="button" onclick={createRatePolicy} disabled={loading}>创建策略</button>
		{/snippet}
	</Drawer>

<Drawer open={modelDrawerOpen} title="创建模型别名" subtitle="下游名称、LiteLLM 映射、能力标记与 IP 策略" wide onClose={() => (modelDrawerOpen = false)}>
	<div class="drawer-form">
		<label>别名<input bind:value={modelForm.alias} placeholder="dev-model" /></label>
		<label>上游模型名<input bind:value={modelForm.upstream_model_name} /></label>
		<label>上游格式
			<select bind:value={modelForm.upstream_format}>
				<option value="openai">{UPSTREAM_FORMAT_LABEL.openai}</option>
				<option value="openai_chat_completions">{UPSTREAM_FORMAT_LABEL.openai_chat_completions}</option>
				<option value="anthropic">{UPSTREAM_FORMAT_LABEL.anthropic}</option>
				<option value="hosted_vllm">{UPSTREAM_FORMAT_LABEL.hosted_vllm}</option>
			</select>
		</label>
		<label>粘性生命周期秒数<input type="number" min="1" max="86400" bind:value={modelForm.sticky_ttl_seconds} /></label>
		<label>IP 策略<select bind:value={modelForm.ip_policy_mode}><option value="all_pass">全部放行</option><option value="allowlist">白名单</option></select></label>
		<label>备注<input bind:value={modelForm.notes} /></label>
		{#if modelForm.ip_policy_mode === 'allowlist'}
			<label class="span-2">CIDRs<textarea bind:value={modelForm.ip_allowlist_cidrs} placeholder="10.0.0.0/8"></textarea></label>
		{/if}
		<div class="actions span-2">
			<label class="check-label"><input type="checkbox" bind:checked={modelForm.supports_streaming} /> Streaming</label>
			<label class="check-label"><input type="checkbox" bind:checked={modelForm.supports_tools} /> Tools</label>
			<label class="check-label"><input type="checkbox" bind:checked={modelForm.supports_reasoning} /> Reasoning</label>
		</div>
	</div>
	{#snippet footer()}
		<button class="secondary" type="button" onclick={() => (modelDrawerOpen = false)}>取消</button>
		<button type="button" onclick={createModel} disabled={loading}>创建别名</button>
	{/snippet}
</Drawer>
{#if detail?.kind === 'team' && detailTeam}
	<Drawer open wide title={detailTeam.name} subtitle="成员 · 模型授权 · 分时段配额" onClose={() => (detail = null)}>
		<div class="tabs">
			<button type="button" class:active={teamDrawerTab === 'members'} onclick={() => (teamDrawerTab = 'members')}>成员</button>
			<button type="button" class:active={teamDrawerTab === 'grants'} onclick={() => (teamDrawerTab = 'grants')}>模型授权</button>
			<button type="button" class:active={teamDrawerTab === 'quota'} onclick={() => (teamDrawerTab = 'quota')}>Token 配额</button>
		</div>
		{#if teamDrawerTab === 'members'}
			<div class="drawer-form">
				<label>搜索用户<input bind:value={teamSubjectSearch} placeholder="输入姓名或工号" oninput={() => queueSubjectOptions(teamSubjectSearch)} /></label>
				<label>用户<select bind:value={teamMembershipForm.subject_id}><option value="">用户</option>{#each subjectOptions(teamSubjectSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label>
				<label>角色<select bind:value={teamMembershipForm.role}>{#each managedRoles as role}<option value={role.value}>{role.label}</option>{/each}</select></label>
				<div class="actions"><button type="button" onclick={createTeamMembership} disabled={loading}>添加成员</button></div>
			</div>
			<div class="table-wrap">
				<table>
					<thead><tr><th>用户</th><th>角色</th><th>状态</th></tr></thead>
					<tbody>
						{#each teamDrawerMembers as membership}
							<tr>
								<td>{membershipSubjectLabel(membership)}</td>
								<td>{membership.role}</td>
								<td><Switch checked={membership.state === 'active'} label="切换成员状态" onToggle={() => void setTeamMembershipState(membership.id, membership.state === 'active' ? 'disabled' : 'active')} /></td>
							</tr>
						{:else}
							<tr><td colspan="3" class="empty">该权限组还没有成员。</td></tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else if teamDrawerTab === 'grants'}
			<div class="drawer-form">
				<label>模型<select bind:value={modelTeamGrantForm.model_alias_id}><option value="">模型</option>{#each modelOptions as model}<option value={model.id}>{model.alias}</option>{/each}</select></label>
				<div class="actions"><button type="button" onclick={createModelTeamGrant} disabled={loading}>授权模型</button></div>
			</div>
			<div class="table-wrap">
				<table>
					<thead><tr><th>模型</th><th>状态</th></tr></thead>
					<tbody>
						{#each teamDrawerGrants as grant}
							<tr>
								<td>{grant.model_alias ?? modelLabel(grant.model_alias_id)}</td>
								<td><Switch checked={grant.state === 'active'} label="切换授权状态" onToggle={() => void setModelTeamGrantState(grant.id, grant.state === 'active' ? 'disabled' : 'active')} /></td>
							</tr>
						{:else}
							<tr><td colspan="2" class="empty">该权限组还没有模型授权。</td></tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else}
			<p class="muted">时间窗：上午 8:00–13:00 · 下午 13:00–18:00 · 晚上 18:00–次日 8:00。留空 = 该时段不限量。上限对组内<b>每个成员</b>分别生效：例如设 50M，则每个成员各自可用 50M；同属 A(400)/B(500) 两组的成员合计可用到 500。</p>
			<div class="drawer-form">
				<label>上午 token 上限<input type="number" min="0" bind:value={teamQuotaForm.morning} placeholder="留空不限" /></label>
				<label>下午 token 上限<input type="number" min="0" bind:value={teamQuotaForm.afternoon} placeholder="留空不限" /></label>
				<label>晚上 token 上限<input type="number" min="0" bind:value={teamQuotaForm.evening} placeholder="留空不限" /></label>
			</div>
			<QuotaChips row={teamQuotaRow(detailTeam.id)} />
			{#if teamDrawerQuotaUsage && teamDrawerQuotaUsage.limit !== null}
				<div class="quota-usage-block">
					<h3>当前窗口成员用量 <span class="muted" style="font-size:12px;">（上限 {fmtNumber(teamDrawerQuotaUsage.limit)} 每人）</span></h3>
					<div class="table-wrap">
						<table>
							<thead><tr><th>成员</th><th>已用 / 上限</th><th style="width:40%">进度</th></tr></thead>
							<tbody>
								{#each teamDrawerMembers as member}
									{@const usage = teamDrawerQuotaUsage.members.find((m) => m.subject_id === member.subject_id)}
									{@const used = usage?.used ?? 0}
									{@const pct = Math.min(100, (used / teamDrawerQuotaUsage.limit) * 100)}
									{@const tone = used >= teamDrawerQuotaUsage.limit ? 'danger' : used >= teamDrawerQuotaUsage.limit * 0.8 ? 'warn' : ''}
									<tr>
										<td>{membershipSubjectLabel(member)}</td>
										<td class="mono">{fmtNumber(used)} / {fmtNumber(teamDrawerQuotaUsage.limit)}</td>
										<td><div class="bar-track {tone}"><span style={`width: ${pct}%;`}></span></div></td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</div>
			{/if}
			{#snippet footer()}
				<button class="secondary" type="button" onclick={() => (detail = null)}>关闭</button>
				<button type="button" onclick={saveTeamQuota} disabled={loading}>保存配额</button>
			{/snippet}
		{/if}
	</Drawer>
{:else}
	<Drawer open={detail !== null} title={detailTitle} onClose={() => (detail = null)}>
		{#if detailSubject}
			<dl class="detail-list">
				<div class="detail-item"><dt>姓名</dt><dd>{detailSubject.name}</dd></div>
				<div class="detail-item"><dt>工号</dt><dd>{detailSubject.login_username ?? '无'}</dd></div>
				<div class="detail-item"><dt>类型</dt><dd>{subjectTypeLabel(detailSubject.type)}</dd></div>
				<div class="detail-item"><dt>状态</dt><dd><StateBadge value={detailSubject.state} /></dd></div>
				<div class="detail-item"><dt>限流覆盖</dt><dd>{#if inventory.subjectRateOverrides[detailSubject.id]}<span class="rate-override"><span>C <strong>{inventory.subjectRateOverrides[detailSubject.id].concurrency ?? '继承'}</strong></span><span>R <strong>{inventory.subjectRateOverrides[detailSubject.id].rpm ?? '继承'}</strong></span></span>{:else}继承{/if}</dd></div>
				<div class="detail-item"><dt>备注</dt><dd>{detailSubject.notes || '—'}</dd></div>
				<div class="detail-item"><dt>ID</dt><dd class="mono">{detailSubject.id}</dd></div>
			</dl>
		{:else if detailProject}
			<dl class="detail-list">
				<div class="detail-item"><dt>名称</dt><dd>{detailProject.name}</dd></div>
				<div class="detail-item"><dt>负责人</dt><dd>{subjectLabel(detailProject.owner_subject_id)}</dd></div>
				<div class="detail-item"><dt>状态</dt><dd><StateBadge value={detailProject.state} /></dd></div>
				<div class="detail-item"><dt>备注</dt><dd>{detailProject.notes || '—'}</dd></div>
				<div class="detail-item"><dt>ID</dt><dd class="mono">{detailProject.id}</dd></div>
			</dl>
			<h3>成员</h3>
			<div class="table-wrap">
				<table>
					<thead><tr><th>用户</th><th>角色</th></tr></thead>
					<tbody>
						{#each detailMembers as membership}
							<tr><td>{membershipSubjectLabel(membership)}</td><td>{membership.role}</td></tr>
						{:else}
							<tr><td colspan="2" class="empty">暂无成员。</td></tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else if detailKey}
			<dl class="detail-list">
				<div class="detail-item"><dt>名称</dt><dd>{detailKey.name}</dd></div>
				<div class="detail-item"><dt>前缀</dt><dd><code>{detailKey.key_prefix}</code></dd></div>
				<div class="detail-item"><dt>用户</dt><dd>{subjectLabel(detailKey.subject_id)}</dd></div>
				<div class="detail-item"><dt>项目</dt><dd>{projectLabel(detailKey.project_id)}</dd></div>
				<div class="detail-item"><dt>状态</dt><dd><StateBadge value={detailKey.state} /></dd></div>
				<div class="detail-item"><dt>创建于</dt><dd>{toDateTimeLocal(new Date(detailKey.created_at))}</dd></div>
				<div class="detail-item"><dt>ID</dt><dd class="mono">{detailKey.id}</dd></div>
			</dl>
		{:else if detailModel}
			<dl class="detail-list">
				<div class="detail-item"><dt>别名</dt><dd>{detailModel.alias}</dd></div>
				<div class="detail-item"><dt>上游模型名</dt><dd>{detailModel.upstream_model_name}</dd></div>
				<div class="detail-item"><dt>LiteLLM</dt><dd class="mono">{detailModel.litellm_model}</dd></div>
				<div class="detail-item"><dt>能力</dt><dd><span class="cap-dots"><span class={detailModel.supports_streaming ? 'cap-on' : 'cap-off'}>S</span><span class={detailModel.supports_tools ? 'cap-on' : 'cap-off'}>T</span><span class={detailModel.supports_reasoning ? 'cap-on' : 'cap-off'}>R</span></span></dd></div>
				<div class="detail-item"><dt>粘性 TTL</dt><dd class="mono">{detailModel.sticky_ttl_seconds}s</dd></div>
				<div class="detail-item"><dt>IP 策略</dt><dd><StateBadge value={detailModel.ip_policy_mode} /></dd></div>
				<div class="detail-item"><dt>CIDRs</dt><dd class="mono">{detailModel.ip_allowlist_cidrs.join(', ') || '未配置'}</dd></div>
				<div class="detail-item"><dt>状态</dt><dd><StateBadge value={detailModel.state} /></dd></div>
				<div class="detail-item"><dt>备注</dt><dd>{detailModel.notes || '—'}</dd></div>
				<div class="detail-item"><dt>ID</dt><dd class="mono">{detailModel.id}</dd></div>
			</dl>
		{/if}
	</Drawer>
{/if}

{#if rateEditor}
	<div class="modal-backdrop" role="presentation" onclick={() => (rateEditor = null)}>
		<section class="modal" aria-label="编辑限流覆盖" onclick={(event) => event.stopPropagation()}>
			<header><h2>编辑限流覆盖</h2><p>{rateEditor.name} · 留空 = 继承，PUT 全量合并</p></header>
			<div class="form-grid">
				<label>每分钟请求数<input type="number" min="1" bind:value={rateEditor.rpm} placeholder="留空继承" /></label>
				<label>并发上限<input type="number" min="1" bind:value={rateEditor.concurrency} placeholder="留空继承" /></label>
			</div>
			<footer class="actions">
				<button type="button" onclick={saveRateEditor}>保存</button>
				<button class="secondary" type="button" onclick={() => (rateEditor = null)}>取消</button>
			</footer>
		</section>
	</div>
{/if}

{#if passwordReset}
	<div class="modal-backdrop" role="presentation" onclick={() => (passwordReset = null)}>
		<section class="modal" aria-label="重置密码" onclick={(event) => event.stopPropagation()}>
			<header><h2>重置密码</h2><p>{passwordReset.name}</p></header>
			<label>新密码<input type="password" bind:value={passwordReset.password} placeholder="至少 8 个字符" /></label>
			<footer class="actions">
				<button type="button" onclick={submitPasswordReset} disabled={loading}>重置密码</button>
				<button class="secondary" type="button" onclick={() => (passwordReset = null)}>取消</button>
			</footer>
		</section>
	</div>
{/if}

{#if ttlEditor}
	<div class="modal-backdrop" role="presentation" onclick={() => (ttlEditor = null)}>
		<section class="modal" aria-label="编辑粘性 TTL" onclick={(event) => event.stopPropagation()}>
			<header><h2>编辑粘性生命周期</h2><p>{ttlEditor.alias}</p></header>
			<label>秒数<input type="number" min="1" bind:value={ttlEditor.value} /></label>
			<footer class="actions">
				<button type="button" onclick={saveTtlEditor}>保存</button>
				<button class="secondary" type="button" onclick={() => (ttlEditor = null)}>取消</button>
			</footer>
		</section>
	</div>
{/if}

{#if textEditor}
	<div class="modal-backdrop" role="presentation" onclick={() => (textEditor = null)}>
		<section class="modal" aria-label={textEditor.title} onclick={(event) => event.stopPropagation()}>
			<header><h2>{textEditor.title}</h2></header>
			<label>{textEditor.label}{#if textEditor.multiline}<textarea bind:value={textEditor.value}></textarea>{:else}<input bind:value={textEditor.value} onkeydown={(event) => { if (event.key === 'Enter' && textEditor) { textEditor.onSave(textEditor.value); textEditor = null; } }} />{/if}</label>
			<footer class="actions">
				<button type="button" onclick={() => { if (textEditor) { textEditor.onSave(textEditor.value); textEditor = null; } }}>保存</button>
				<button class="secondary" type="button" onclick={() => (textEditor = null)}>取消</button>
			</footer>
		</section>
	</div>
{/if}

{#if auditDetail}
	<div class="modal-backdrop" role="presentation" onclick={() => (auditDetail = null)}>
		<section class="modal" aria-label="审计详情" onclick={(event) => event.stopPropagation()}>
			<header><h2>{auditDetail.action}</h2><p>{auditDetail.resource_type} · {auditDetail.created_at}</p></header>
			<JsonViewer value={auditDetail} />
			<footer><button class="secondary" type="button" onclick={() => (auditDetail = null)}>关闭</button></footer>
		</section>
	</div>
{/if}

{#if cidrEditorModel}
	<div class="modal-backdrop" role="presentation" onclick={() => (cidrEditorModel = null)}>
		<section class="modal" aria-label="编辑 CIDR 白名单" onclick={(event) => event.stopPropagation()}>
			<header>
				<h2>编辑 CIDR 白名单</h2>
				<p>{cidrEditorModel.alias}，每行一个 CIDR。留空表示全部放行。</p>
			</header>
			<label>CIDR 列表<textarea bind:value={cidrEditorValue} placeholder="10.0.0.0/8&#10;192.168.1.0/24"></textarea></label>
			<footer class="actions">
				<button type="button" onclick={saveModelCidrs}>保存</button>
				<button class="secondary" type="button" onclick={() => (cidrEditorModel = null)}>取消</button>
			</footer>
		</section>
	</div>
{/if}

{#if connected && mustProvideRealName}
	<div class="modal-backdrop" role="presentation">
		<section class="modal" aria-label="补充真实姓名">
			<header>
				<h2>请补充真实姓名</h2>
				<p>为了审计用量能对应到具体人员，继续使用前必须填写真实姓名。</p>
			</header>
			<label>真实姓名<input bind:value={realNameForm.full_name} autocomplete="name" onkeydown={(event) => event.key === 'Enter' && submitRealName()} /></label>
			{#if realNameError}<p class="error">{realNameError}</p>{/if}
			<footer class="actions">
				<button type="button" onclick={submitRealName} disabled={loading}>{loading ? '保存中' : '保存并继续'}</button>
			</footer>
		</section>
	</div>
{/if}

<ConfirmModal
	open={confirmState !== null}
	title={confirmState?.title ?? ''}
	message={confirmState?.message ?? ''}
	confirmLabel={confirmState?.confirmLabel ?? '确认'}
	onConfirm={runConfirm}
	onCancel={closeConfirm}
/>

<SecretOnceDialog secret={plaintextKey} onClose={() => (plaintextKey = '')} />

<Toast />
