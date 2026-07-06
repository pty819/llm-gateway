<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import {
		Activity,
		BookOpen,
		Copy,
		Database,
		Gauge,
		KeyRound,
		Network,
		Package,
		Plug,
		Route,
		Shield,
		Trophy,
		UserPlus,
		Users
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
		LoginResponse,
		ManagedRankingRow,
		OwnUsageSummary,
		PaginatedResponse,
		Project,
		ProjectMembership,
		ReadyStatus,
		RegisterResponse,
		ResourceState,
		RuntimeMetricsSnapshot,
		Subject,
		SubjectType,
		Team,
		TeamMembership,
		UpstreamHealth
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
	import {
		clearStoredSessionToken,
		loadStoredSessionToken,
		persistSessionToken
	} from '$lib/state/admin-token';
	import { parseCidrList, parseJsonObject, validateCidrList, validateHttpUrl } from '$lib/validators';
	import { bareModelName } from '$lib/upstream-format';
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
		matchNeedle,
		pageRows,
		pageCount,
		pageCountTotal,
		filteredSubjects as filteredSubjectsConfig,
		subjectOptions as subjectOptionsConfig,
		toDateTimeLocal,
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
		icon: typeof Activity;
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
	let analyticsBucket = $state<'minute' | 'hour' | 'day'>('hour');
	let analyticsDimension = $state<'model' | 'subject' | 'project' | 'endpoint' | 'outcome' | 'streaming'>('model');
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
	let cidrEditorModel = $state<Inventory['models'][number] | null>(null);
	let cidrEditorValue = $state('');
	let realNameForm = $state({ full_name: '' });
	let realNameError = $state('');

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
	let keyForm = $state({ subject_id: '', project_id: '', name: '' });
	let modelForm = $state({
		alias: '',
		upstream_model_name: '',
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
	const preferredModel = $derived(profile?.models[0] ?? '<model-alias>');
	const visibleKeyHint = $derived(profile?.keys[0]?.key_prefix ? `${profile.keys[0].key_prefix}...` : 'gw-...');
	const codexEnvCommand = $derived(`export LLM_GATEWAY_API_KEY="<粘贴你的完整网关密钥>"`);
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
		subjects: inventory.subjects,
		managedSubjectCandidates,
		selfSubjectId: profile?.subject.id,
		selfSubject: profile?.subject ?? null,
		projects: inventory.projects,
		keys: inventory.keys,
		models: inventory.models,
		teams: inventory.teams
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
	const visibleUsageRows = $derived(
		usageRows.toSorted((a, b) => Number(b.total_tokens ?? 0) - Number(a.total_tokens ?? 0)).slice(0, PAGE_SIZE.usagePreview)
	);
	const visibleAnalyticsBuckets = $derived(inventory.analyticsBuckets.slice(0, PAGE_SIZE.usagePreview));
	const visibleAnalyticsDrilldown = $derived(inventory.analyticsDrilldown.slice(0, PAGE_SIZE.usagePreview));
	const realtimeRows = $derived.by(() => {
		const live = realtime?.upstreams ?? [];
		if (!realtimeLocked) return live;
		// 锁定:全部活动配置端点按名排序,合并 realtime 指标(无数据则填占位行)
		return inventory.upstreams
			.filter((u) => u.state === 'active')
			.toSorted((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN'))
			.map((u) => {
				const match = live.find((r) => r.upstream_id === u.id);
				return (
					match ?? {
						upstream_id: u.id,
						upstream_name: u.name,
						model_alias: '', // inventory 无 model_alias 字段,留空,模板里用占位
						tokens_per_second: null,
						recent_tokens: null,
						active_connections: 0
						// vllm 缺省,模板里用可选链处理
					}
				);
			});
	});
	const realtimeUpdatedLabel = $derived(realtime ? new Date(realtime.generated_at).toLocaleTimeString() : '无');
	const analyticsMaxTokens = $derived(
		Math.max(1, ...visibleAnalyticsBuckets.map((row) => Number(row.total_tokens ?? 0)))
	);
	const subjectRows = $derived(filteredSubjectsConfig(subjectSearch, inventory.subjects).toSorted((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN')));
	const dropdownProjects = $derived(inventory.projects.filter((p) => !p.name.startsWith('user-')));
	const projectRows = $derived(
		inventory.projects
			.filter((project) => matchNeedle(projectSearch, [project.name, project.notes ?? '', subjectLabel(project.owner_subject_id)]))
			.toSorted((a, b) => b.created_at.localeCompare(a.created_at))
	);
	const keyRows = $derived(
		inventory.keys
			.filter((key) => {
				if (keyProjectFilter && key.project_id !== keyProjectFilter) return false;
				if (keyStateFilter && key.state !== keyStateFilter) return false;
				if (!matchNeedle(keyListSubjectSearch, [subjectLabel(key.subject_id), key.name, key.key_prefix])) return false;
				return true;
			})
			.toSorted((a, b) => b.created_at.localeCompare(a.created_at))
	);
	const teamMembershipRows = $derived(
		inventory.teamMemberships
			.filter((membership) => {
				if (teamMembershipTeamFilter && membership.team_id !== teamMembershipTeamFilter) return false;
				if (teamMembershipStateFilter && membership.state !== teamMembershipStateFilter) return false;
				if (teamMembershipRoleFilter && !membership.role.toLowerCase().includes(teamMembershipRoleFilter.trim().toLowerCase())) return false;
				if (!matchNeedle(teamMembershipSubjectSearch, [subjectLabel(membership.subject_id)])) return false;
				return true;
			})
			.toSorted((a, b) => b.created_at.localeCompare(a.created_at))
	);
	const rankingRows = $derived(inventory.ranking.slice(0, PAGE_SIZE.ranking));
	const auditRows = $derived(inventory.audit.toSorted((a, b) => b.created_at.localeCompare(a.created_at)));
	const subjectPageRows = $derived(pageRows(subjectRows, subjectPage, PAGE_SIZE.defaultList));
	const projectPageRows = $derived(pageRows(projectRows, projectPage, PAGE_SIZE.defaultList));
	const keyPageRows = $derived(pageRows(keyRows, keyPage, PAGE_SIZE.defaultList));
	const teamMembershipPageRows = $derived(pageRows(teamMembershipRows, teamMembershipPage, PAGE_SIZE.defaultList));
	const rankingPageRows = $derived(pageRows(rankingRows, rankingPage, PAGE_SIZE.ranking));
	const auditPageRows = $derived(pageRows(auditRows, auditPage, PAGE_SIZE.audit));
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

	onMount(() => {
		const range = defaultUsageRange();
		usageStart = range.start;
		usageEnd = range.end;
		ownUsageStart = range.start;
		ownUsageEnd = range.end;
		gatewayBaseUrl = inferGatewayBaseUrl();
		sessionToken = loadStoredSessionToken();
		rememberSession = Boolean(sessionToken);
		void refreshReady();
		if (sessionToken) void loadProfile(true);
	});

	onDestroy(() => {
		stopRealtimeStream();
	});

	async function loginAccount(fromStorage = false) {
		if (!loginForm.username.trim() || !loginForm.password) {
			pageError = '请输入用户名和密码。';
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
			pageError = '工号必须是 1 个字母加 8 位数字，例如 l00014624。';
			return;
		}
		if (!registerForm.full_name.trim() || registerForm.password.length < 8) {
			pageError = '请输入真实姓名，密码至少 8 个字符。';
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
		});
		healthCheckToggling = false;
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
			const [
				subjectsPage,
				projectsPage,
				membershipsPage,
				keysPage,
				models,
				entitlements,
				teamsPage,
				teamMembershipsPage,
				modelTeamGrants,
				upstreams,
				ratePolicies,
				audit,
				hcConfig
			] = await Promise.all([
				api.get<PaginatedResponse<Subject>>('/admin/subjects'),
				api.get<PaginatedResponse<Project>>('/admin/projects'),
				api.get<PaginatedResponse<ProjectMembership>>('/admin/project-memberships'),
				api.get<PaginatedResponse<GatewayKey>>('/admin/gateway-keys'),
				api.get<Inventory['models']>('/admin/model-aliases'),
				api.get<Inventory['entitlements']>('/admin/model-entitlements'),
				api.get<PaginatedResponse<Team>>('/admin/teams'),
				api.get<PaginatedResponse<TeamMembership>>('/admin/team-memberships'),
				api.get<Inventory['modelTeamGrants']>('/admin/model-team-grants'),
				api.get<Inventory['upstreams']>('/admin/upstreams'),
				api.get<Inventory['ratePolicies']>('/admin/rate-policies'),
				api.get<Inventory['audit']>('/admin/audit-events'),
				api.get<HealthCheckConfig>('/admin/health-check')
			]);
			healthCheckConfig = hcConfig;
			inventory = {
				subjects: subjectsPage.items,
				projects: projectsPage.items,
				memberships: membershipsPage.items,
				keys: keysPage.items,
				models,
				entitlements,
				teams: teamsPage.items,
				teamMemberships: teamMembershipsPage.items,
				modelTeamGrants,
				upstreams,
				ratePolicies,
				usage: inventory.usage,
				usageTotals: inventory.usageTotals,
				ranking: inventory.ranking,
				analyticsBuckets: inventory.analyticsBuckets,
				analyticsDrilldown: inventory.analyticsDrilldown,
				audit
			};
		});
	}

	async function refreshUsageAnalytics() {
		await run(async () => {
			const analyticsParams = {
				start: usageStart,
				end: usageEnd,
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
					limit: PAGE_SIZE.usagePreview
				}),
				api.get<Inventory['analyticsDrilldown']>('/admin/analytics/drilldown', {
					...analyticsParams,
					dimension: analyticsDimension,
					limit: PAGE_SIZE.usagePreview
				})
			]);
			inventory = { ...inventory, usageTotals, usage, analyticsBuckets: buckets, analyticsDrilldown: drilldown };
		});
	}

	async function refreshUsageRanking() {
		await run(async () => {
			const ranking = await api.get<Inventory['ranking']>('/admin/usage/ranking', {
				start: usageStart,
				end: usageEnd,
				model: rankingModel,
				limit: rankingLimit
			});
			inventory = { ...inventory, ranking };
		});
	}

	async function createSubject() {
		if (subjectForm.login_username && !employeeIdPattern.test(subjectForm.login_username.trim())) {
			pageError = '工号必须是 1 个字母加 8 位数字，例如 l00014624。';
			return;
		}
		await run(async () => {
			await api.post('/admin/subjects', clean({ ...subjectForm }));
			subjectForm = { name: '', login_username: '', password: '', type: 'user', notes: '' };
			await refreshAll();
		});
	}

	async function patchSubject(id: string, patch: Record<string, unknown>) {
		await run(async () => {
			await api.patch(`/admin/subjects/${id}`, patch);
			await refreshAll();
		});
	}

	async function setSubjectState(id: string, state: ResourceState) {
		await run(async () => {
			await api.patch(`/admin/subjects/${id}/state`, { state });
			await refreshAll();
		});
	}

	async function resetSubjectPassword() {
		if (!subjectPasswordForm.subject_id || subjectPasswordForm.new_password.length < 8) {
			pageError = '请选择用户，新密码至少 8 个字符。';
			return;
		}
		await run(async () => {
			await api.patch(`/admin/subjects/${subjectPasswordForm.subject_id}/password`, {
				new_password: subjectPasswordForm.new_password
			});
			subjectPasswordForm = { subject_id: '', new_password: '' };
		});
	}

	async function deleteSubject(subject: Inventory['subjects'][number]) {
		if (!window.confirm(`确认删除用户 ${subject.name}（${subject.login_username ?? '无工号'}）？`)) return;
		await run(async () => {
			await api.delete(`/admin/subjects/${subject.id}`);
			await refreshAll();
		});
	}

	async function createProject() {
		await run(async () => {
			await api.post('/admin/projects', clean({ ...projectForm }));
			projectForm = { name: '', owner_subject_id: '', notes: '' };
			await refreshAll();
		});
	}

	async function patchProject(id: string, patch: Record<string, unknown>) {
		await run(async () => {
			await api.patch(`/admin/projects/${id}`, clean(patch));
			await refreshAll();
		});
	}

	async function createMembership() {
		await run(async () => {
			await api.post('/admin/project-memberships', clean({ ...membershipForm }));
			membershipForm = { project_id: '', subject_id: '', role: 'member' };
			await refreshAll();
		});
	}

	async function issueKey() {
		await run(async () => {
			const response = await api.post<GatewayKeyCreateResponse>('/admin/gateway-keys', clean({ ...keyForm }));
			plaintextKey = response.plaintext_key;
			keyForm = { subject_id: '', project_id: '', name: '' };
			await refreshAll();
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
			pageError = '请输入当前密码，新密码至少 8 个字符。';
			return;
		}
		await run(async () => {
			await api.patch('/auth/password', ownPasswordForm);
			ownPasswordForm = { current_password: '', new_password: '' };
		});
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
			await refreshAll();
		});
	}

	async function createModel() {
		const cidrCheck = modelForm.ip_policy_mode === 'allowlist' ? validateCidrList(modelForm.ip_allowlist_cidrs) : { ok: true };
		if (!cidrCheck.ok) {
			pageError = cidrCheck.message ?? 'CIDR 列表不合法';
			return;
		}
		await run(async () => {
			await api.post(
				'/admin/model-aliases',
				clean({
					alias: modelForm.alias,
					upstream_model_name: modelForm.upstream_model_name,
					litellm_model: modelForm.upstream_model_name,
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
				supports_streaming: true,
				supports_tools: true,
				supports_reasoning: true,
				sticky_ttl_seconds: 1200,
				ip_policy_mode: 'all_pass',
				ip_allowlist_cidrs: '',
				notes: ''
			};
			await refreshAll();
		});
	}

	async function patchModel(id: string, patch: Record<string, unknown>) {
		await run(async () => {
			await api.patch(`/admin/model-aliases/${id}`, patch);
			await refreshAll();
		});
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
			pageError = cidrCheck.message ?? 'CIDR 列表不合法';
			return;
		}
		await patchModel(cidrEditorModel.id, {
			ip_policy_mode: 'allowlist',
			ip_allowlist_cidrs: parseCidrList(trimmed)
		});
		cidrEditorModel = null;
	}

	async function deleteModel(model: Inventory['models'][number]) {
		if (!window.confirm(`确认删除模型别名 ${model.alias}？`)) return;
		await run(async () => {
			try {
				await api.delete(`/admin/model-aliases/${model.id}`);
			} catch (error) {
				if (isModelUpstreamConflict(error)) {
					const upstreamCount = Number((error.detail as { upstream_count?: number }).upstream_count ?? 0);
					if (window.confirm(`这个模型还有 ${upstreamCount} 个上游端点依赖。是否一起删除这些上游依赖？`)) {
						await api.delete(`/admin/model-aliases/${model.id}`, { cascade_upstreams: true });
					} else {
						return;
					}
				} else {
					throw error;
				}
			}
			await refreshAll();
		});
	}

	async function deleteUpstream(upstream: Inventory['upstreams'][number]) {
		if (!window.confirm(`确认删除上游端点 ${upstream.name}？`)) return;
		await run(async () => {
			await api.delete(`/admin/upstreams/${upstream.id}`);
			await refreshAll();
		});
	}

	async function createUpstream() {
		const urlCheck = validateHttpUrl(upstreamForm.base_url, '上游地址');
		if (!urlCheck.ok) {
			pageError = urlCheck.message ?? '上游地址不合法';
			return;
		}
		if (upstreamForm.metrics_url.trim()) {
			const metricsUrlCheck = validateHttpUrl(upstreamForm.metrics_url, 'Metrics URL');
			if (!metricsUrlCheck.ok) {
				pageError = metricsUrlCheck.message ?? 'Metrics URL 不合法';
				return;
			}
		}
		if (!upstreamForm.health_path.startsWith('/')) {
			pageError = '健康检查路径必须以 / 开头。';
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
			await refreshAll();
		});
	}

	async function setUpstreamState(id: string, state: ResourceState) {
		await run(async () => {
			await api.patch(`/admin/upstreams/${id}`, { state });
			await refreshAll();
		});
	}

	async function patchUpstream(id: string, patch: Record<string, unknown>) {
		await run(async () => {
			await api.patch(`/admin/upstreams/${id}`, clean(patch));
			await refreshAll();
		});
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
			await refreshAll();
		});
	}

	async function setEntitlementState(id: string, state: ResourceState) {
		await run(async () => {
			await api.patch(`/admin/model-entitlements/${id}/state`, { state });
			await refreshAll();
		});
	}

	async function createTeam() {
		await run(async () => {
			await api.post('/admin/teams', clean({ ...teamForm }));
			teamForm = { name: '', notes: '' };
			await refreshAll();
		});
	}

	async function patchTeam(id: string, patch: Record<string, unknown>) {
		await run(async () => {
			await api.patch(`/admin/teams/${id}`, clean(patch));
			await refreshAll();
		});
	}

	async function createTeamMembership() {
		await run(async () => {
			await api.post('/admin/team-memberships', clean({ ...teamMembershipForm }));
			teamMembershipForm = { team_id: '', subject_id: '', role: 'member' };
			await refreshAll();
		});
	}

	async function setTeamMembershipState(id: string, state: ResourceState) {
		await run(async () => {
			await api.patch(`/admin/team-memberships/${id}/state`, { state });
			await refreshAll();
		});
	}

	async function createModelTeamGrant() {
		await run(async () => {
			await api.post('/admin/model-team-grants', clean({ ...modelTeamGrantForm }));
			modelTeamGrantForm = { model_alias_id: '', team_id: '' };
			await refreshAll();
		});
	}

	async function setModelTeamGrantState(id: string, state: ResourceState) {
		await run(async () => {
			await api.patch(`/admin/model-team-grants/${id}/state`, { state });
			await refreshAll();
		});
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
			await refreshAll();
		});
	}

	async function setRateState(id: string, state: ResourceState) {
		await run(async () => {
			await api.patch(`/admin/rate-policies/${id}`, { state });
			await refreshAll();
		});
	}

	async function refreshOwnUsage() {
		await run(fetchOwnUsage);
	}

	async function fetchOwnUsage() {
		ownUsage = await api.get<OwnUsageSummary>('/auth/usage/summary', {
			start: ownUsageStart,
			end: ownUsageEnd
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
			pageError = '请选择项目和用户。';
			return;
		}
		await run(async () => {
			await api.post('/auth/managed/project-memberships', {
				resource_id: managedProjectMemberForm.resource_id,
				subject_id: managedProjectMemberForm.subject_id,
				role: managedProjectMemberForm.role || 'member'
			});
			await refreshManagedProjectMemberships();
		});
	}

	async function removeManagedProjectMember(membership: ProjectMembership) {
		if (!window.confirm(`确认从项目中移除 ${membershipSubjectLabel(membership)}？`)) return;
		await run(async () => {
			await api.delete(`/auth/managed/project-memberships/${membership.id}`);
			await refreshManagedProjectMemberships();
		});
	}

	async function addManagedTeamMember() {
		if (!managedTeamMemberForm.resource_id || !managedTeamMemberForm.subject_id) {
			pageError = '请选择权限组和用户。';
			return;
		}
		await run(async () => {
			await api.post('/auth/managed/team-memberships', {
				resource_id: managedTeamMemberForm.resource_id,
				subject_id: managedTeamMemberForm.subject_id,
				role: managedTeamMemberForm.role || 'member'
			});
			await refreshManagedTeamMemberships();
		});
	}

	async function setManagedTeamMemberState(membership: TeamMembership, state: ResourceState) {
		await run(async () => {
			await api.patch(`/auth/managed/team-memberships/${membership.id}`, { state });
			await refreshManagedTeamMemberships();
		});
	}

	function setUsageRange(days: number) {
		const range = usageRangeForDays(days);
		usageStart = range.start;
		usageEnd = range.end;
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

	async function run(fn: () => Promise<void>) {
		loading = true;
		pageError = '';
		try {
			await fn();
		} catch (error) {
			pageError = errorMessage(error);
		} finally {
			loading = false;
		}
	}

	function emptyInventory(): Inventory {
		return {
			subjects: [],
			projects: [],
			memberships: [],
			keys: [],
			models: [],
			entitlements: [],
			teams: [],
			teamMemberships: [],
			modelTeamGrants: [],
			upstreams: [],
			ratePolicies: [],
			usage: [],
			usageTotals: null,
			ranking: [],
			analyticsBuckets: [],
			analyticsDrilldown: [],
			audit: []
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
		if (scope === 'subject') return subjectOptions(subjectQuery).map((item) => ({ id: item.id, label: subjectDisplay(item) }));
		if (scope === 'project') return dropdownProjects.map((item) => ({ id: item.id, label: item.name }));
		return inventory.keys.map((item) => ({ id: item.id, label: `${item.name} (${item.key_prefix})` }));
	}

	function subjectOptions(query: string) {
		return subjectOptionsConfig(query, inventory.subjects);
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
				<strong>LLM Gateway</strong>
				<span>{diagnostics?.environment ?? '环境未知'}</span>
			</div>
			{#if !isAdmin}
				<nav class="nav-group" aria-label="账号">
					<div class="nav-group-title">账号</div>
					<button class:active={active === 'usage'} class="nav-button" type="button" onclick={() => (active = 'usage')}><span>我的访问权限</span><KeyRound size={16} /></button>
				</nav>
				<nav class="nav-group" aria-label="市场">
					<div class="nav-group-title">市场</div>
					<button class:active={active === 'skill-market'} class="nav-button" type="button" onclick={() => (active = 'skill-market')}><span>Skill 市场</span><Package size={16} /></button>
					<button class:active={active === 'mcp-market'} class="nav-button" type="button" onclick={() => (active = 'mcp-market')}><span>MCP 市场</span><Plug size={16} /></button>
				</nav>
			{:else}
			{#each navGroups as group}
				<nav class="nav-group" aria-label={group}>
					<div class="nav-group-title">{group}</div>
					{#each sections.filter((section) => section.group === group) as section}
						{@const Icon = section.icon}
						<button class:active={active === section.id} class="nav-button" type="button" onclick={() => (active = section.id)}>
							<span>{section.label}</span>
							<Icon size={16} />
						</button>
					{/each}
				</nav>
			{/each}
			{/if}
		</aside>
		<main class="main">
			<div class="topbar">
				<div class="actions">
					<StateBadge value={ready?.ok ? 'ready' : 'not_ready'} tone={ready?.ok ? 'success' : 'danger'} />
					<span class="muted">Postgres {ready?.checks.postgres ? '正常' : '异常'} · Redis {ready?.checks.redis ? '正常' : '异常'}</span>
				</div>
				<div class="actions">
					<button class="secondary" type="button" onclick={refreshAll} disabled={loading}>{loading ? '处理中' : '刷新'}</button>
					<button class="secondary" type="button" onclick={disconnect}>退出登录</button>
				</div>
			</div>
			<section class="content">
				{#if pageError}<div class="error">{pageError}</div>{/if}

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
					{gatewayOrigin}
					{codexEnvCommand}
					{codexConfigCommand}
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
					<PageTitle title={'Skill 市场'} subtitle={'上传和管理你的 Skill 制品,并授权给权限组。'} />
					<SkillMarketSection client={api} teams={marketTeams} />
				{:else if active === 'mcp-market'}
					<PageTitle title={'MCP 市场'} subtitle={'发布和管理你的 MCP 连接配置,并授权给权限组。'} />
					<McpMarketSection client={api} teams={marketTeams} />
				{:else if active === 'models'}
					<PageTitle title={'模型别名'} subtitle={'配置下游模型名称、上游模型映射、能力标记和模型级 IP 策略。'} />
					<section class="panel">
						<h2>创建模型别名</h2>
						<div class="form-grid">
							<label>别名<input bind:value={modelForm.alias} placeholder="dev-model" /></label>
							<label>上游模型名<input bind:value={modelForm.upstream_model_name} /></label>
							<label>粘性生命周期秒数<input type="number" min="1" max="86400" bind:value={modelForm.sticky_ttl_seconds} /></label>
							<label>IP 策略<select bind:value={modelForm.ip_policy_mode}><option value="all_pass">全部放行</option><option value="allowlist">白名单</option></select></label>
							<label>CIDRs<textarea bind:value={modelForm.ip_allowlist_cidrs} placeholder="10.0.0.0/8"></textarea></label>
							<label>备注<input bind:value={modelForm.notes} /></label>
						</div>
						<div class="actions">
							<label style="display:flex; width:auto; align-items:center;"><input type="checkbox" bind:checked={modelForm.supports_streaming} style="width:auto;" /> Streaming</label>
							<label style="display:flex; width:auto; align-items:center;"><input type="checkbox" bind:checked={modelForm.supports_tools} style="width:auto;" /> Tools</label>
							<label style="display:flex; width:auto; align-items:center;"><input type="checkbox" bind:checked={modelForm.supports_reasoning} style="width:auto;" /> Reasoning</label>
							<button type="button" onclick={createModel}>创建别名</button>
						</div>
					</section>
					<section class="panel">
						<h2>模型别名</h2>
						<div class="table-wrap">
							<table>
								<thead><tr><th>别名</th><th>上游模型</th><th>状态</th><th>粘性 TTL</th><th>IP 策略</th><th>Streaming</th><th>Tools</th><th>Reasoning</th><th>操作</th></tr></thead>
								<tbody>
									{#each inventory.models as model}
										<tr>
											<td><strong>{model.alias}</strong><br /><span class="muted">{model.upstream_model_name}</span></td>
											<td><span class="badge">OpenAI</span><br /><span class="muted">{bareModelName(model.litellm_model)}</span></td>
											<td><StateBadge value={model.state} /></td>
											<td>{model.sticky_ttl_seconds}s</td>
											<td><StateBadge value={model.ip_policy_mode} /><br /><span class="muted">{model.ip_allowlist_cidrs.join(', ') || '未配置 CIDR'}</span></td>
											<td><StateBadge value={model.supports_streaming} tone="accent" /></td>
											<td><StateBadge value={model.supports_tools} tone="accent" /></td>
											<td><StateBadge value={model.supports_reasoning} tone="accent" /></td>
											<td class="actions"><button class="secondary" type="button" onclick={() => editModelCidrs(model)}>编辑 CIDR</button><button class="secondary" type="button" onclick={() => {
												const next = prompt('粘性生命周期秒数', String(model.sticky_ttl_seconds));
												if (next !== null) patchModel(model.id, { sticky_ttl_seconds: Number(next) });
											}}>编辑 TTL</button><button class="secondary" type="button" onclick={() => patchModel(model.id, { state: model.state === 'active' ? 'disabled' : 'active' })}>{model.state === 'active' ? '禁用' : '启用'}</button><button class="danger" type="button" onclick={() => deleteModel(model)}>删除</button></td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					</section>
				{:else if active === 'upstreams'}
					<PageTitle title={'上游端点'} subtitle={'模型别名背后的同构 OpenAI 兼容副本池。'} />
					<section class="panel">
						<h2>创建上游</h2>
						<div class="form-grid">
							<label>模型<select bind:value={upstreamForm.model_alias_id}><option value="">选择模型</option>{#each inventory.models as model}<option value={model.id}>{model.alias}</option>{/each}</select></label>
							<label>名称<input bind:value={upstreamForm.name} /></label>
							<label>Base URL<input bind:value={upstreamForm.base_url} placeholder="http://host:8000/v1" /></label>
							<label>Metrics URL<input bind:value={upstreamForm.metrics_url} placeholder="可选，例如 http://router-host:29000/metrics" /></label>
							<label>健康检查路径<input bind:value={upstreamForm.health_path} /></label>
							<label>API key 引用<input bind:value={upstreamForm.api_key_ref} /></label>
							<label>API key 明文<input type="password" bind:value={upstreamForm.api_key_value} /></label>
							<label>额外请求头<textarea bind:value={upstreamForm.extra_headers}></textarea></label>
							<button type="button" onclick={createUpstream}>创建上游</button>
						</div>
					</section>
						<UpstreamTable rows={inventory.upstreams} healthResults={healthResults} modelLabel={modelLabel} onCheck={checkUpstream} onState={setUpstreamState} onPatch={patchUpstream} onDelete={deleteUpstream} onError={(m) => (pageError = m)} />
				{:else if active === 'subjects'}
					<PageTitle title={'用户'} subtitle={'由网关管理的人类用户和服务账号。'} />
						<section class="panel">
							<h2>创建用户</h2>
							<div class="form-grid">
								<label>真实姓名<input bind:value={subjectForm.name} /></label>
								<label>工号<input bind:value={subjectForm.login_username} placeholder="l00014624" /></label>
								<label>初始密码<input type="password" bind:value={subjectForm.password} /></label>
								<label>类型<select bind:value={subjectForm.type}><option value="user">用户</option><option value="service">服务账号</option></select></label>
								<label>备注<input bind:value={subjectForm.notes} /></label>
								<button type="button" onclick={createSubject}>创建用户</button>
							</div>
						</section>
						<section class="panel">
							<h2>重置用户密码</h2>
							<div class="form-grid">
								<label>搜索用户<input bind:value={subjectPasswordSearch} placeholder="输入姓名或工号" /></label>
								<label>用户<select bind:value={subjectPasswordForm.subject_id}><option value="">选择用户</option>{#each subjectOptions(subjectPasswordSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label>
								<label>新密码<input type="password" bind:value={subjectPasswordForm.new_password} /></label>
								<button type="button" onclick={resetSubjectPassword}>重置密码</button>
							</div>
						</section>
						<section class="panel">
							<h2>用户</h2>
							<div class="form-grid"><label>搜索用户<input bind:value={subjectSearch} placeholder="输入姓名、工号或备注" /></label></div>
							<div class="table-wrap"><table><thead><tr><th>真实姓名</th><th>工号</th><th>类型</th><th>状态</th><th>备注</th><th>操作</th></tr></thead><tbody>{#each subjectPageRows as subject}<tr><td>{subject.name}<br /><span class="muted">{short(subject.id)}</span></td><td>{subject.login_username ?? '无'}</td><td>{subjectTypeLabel(subject.type)}</td><td><StateBadge value={subject.state} /></td><td>{subject.notes}</td><td class="actions"><button class="secondary" type="button" onclick={() => patchSubject(subject.id, { name: prompt('真实姓名', subject.name) ?? subject.name })}>编辑姓名</button><button class="secondary" type="button" onclick={() => patchSubject(subject.id, { notes: prompt('备注', subject.notes ?? '') ?? subject.notes })}>编辑备注</button><button class="secondary" type="button" onclick={() => setSubjectState(subject.id, subject.state === 'active' ? 'disabled' : 'active')}>{subject.state === 'active' ? '禁用' : '启用'}</button><button class="danger" type="button" onclick={() => deleteSubject(subject)}>删除</button></td></tr>{:else}<tr><td colspan="6" class="empty">没有匹配的用户。</td></tr>{/each}</tbody></table></div>
							<Pagination total={subjectRows.length} page={subjectPage} size={PAGE_SIZE.defaultList} onPage={(page) => (subjectPage = page)} />
						</section>
				{:else if active === 'projects'}
					<PageTitle title={'项目'} subtitle={'用量归因和项目成员关系。'} />
					<div class="split">
						<section class="panel">
							<h2>创建项目</h2>
							<div class="form-grid">
								<label>名称<input bind:value={projectForm.name} /></label>
								<label>搜索负责人<input bind:value={projectOwnerSearch} placeholder="输入姓名或工号" /></label>
								<label>负责人<select bind:value={projectForm.owner_subject_id}><option value="">无</option>{#each subjectOptions(projectOwnerSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label>
								<label>备注<input bind:value={projectForm.notes} /></label>
								<button type="button" onclick={createProject}>创建项目</button>
							</div>
						</section>
						<section class="panel">
							<h2>添加项目成员</h2>
							<div class="form-grid">
								<label>项目<select bind:value={membershipForm.project_id}><option value="">项目</option>{#each dropdownProjects as project}<option value={project.id}>{project.name}</option>{/each}</select></label>
								<label>搜索用户<input bind:value={projectMemberSearch} placeholder="输入姓名或工号" /></label>
								<label>用户<select bind:value={membershipForm.subject_id}><option value="">用户</option>{#each subjectOptions(projectMemberSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label>
								<label>角色<select bind:value={membershipForm.role}>{#each managedRoles as role}<option value={role.value}>{role.label}</option>{/each}</select></label>
								<button type="button" onclick={createMembership}>添加成员</button>
							</div>
						</section>
					</div>
					<section class="panel"><h2>项目</h2><div class="form-grid"><label>搜索项目<input bind:value={projectSearch} placeholder="项目名、负责人或备注" /></label></div><div class="table-wrap"><table><thead><tr><th>名称</th><th>负责人</th><th>状态</th><th>备注</th><th>操作</th></tr></thead><tbody>{#each projectPageRows as project}<tr><td>{project.name}<br /><span class="muted">{short(project.id)}</span></td><td>{subjectLabel(project.owner_subject_id)}</td><td><StateBadge value={project.state} /></td><td>{project.notes}</td><td><button class="secondary" type="button" onclick={() => patchProject(project.id, { notes: prompt('备注', project.notes ?? '') ?? project.notes })}>编辑备注</button></td></tr>{:else}<tr><td colspan="5" class="empty">没有匹配的项目。</td></tr>{/each}</tbody></table></div><Pagination total={projectRows.length} page={projectPage} size={PAGE_SIZE.defaultList} onPage={(page) => (projectPage = page)} /></section>
					<section class="panel"><h2>项目成员</h2><div class="table-wrap"><table><thead><tr><th>项目</th><th>用户</th><th>角色</th></tr></thead><tbody>{#each inventory.memberships as membership}<tr><td>{projectLabel(membership.project_id)}</td><td>{subjectLabel(membership.subject_id)}</td><td>{membership.role}</td></tr>{/each}</tbody></table></div></section>
				{:else if active === 'keys'}
					<PageTitle title={'网关密钥'} subtitle={'签发、轮换和停用网关管理的密钥。'} />
					<section class="panel"><h2>签发密钥</h2><div class="form-grid"><label>搜索用户<input bind:value={keySubjectSearch} placeholder="输入姓名或工号" /></label><label>用户<select bind:value={keyForm.subject_id}><option value="">用户</option>{#each subjectOptions(keySubjectSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label><label>项目<select bind:value={keyForm.project_id}><option value="">项目</option>{#each dropdownProjects as project}<option value={project.id}>{project.name}</option>{/each}</select></label><label>名称<input bind:value={keyForm.name} /></label><button type="button" onclick={issueKey}>签发密钥</button></div></section>
					<section class="panel"><h2>密钥</h2><div class="form-grid"><label>搜索用户/密钥<input bind:value={keyListSubjectSearch} placeholder="姓名、工号、密钥名或前缀" /></label><label>项目<select bind:value={keyProjectFilter}><option value="">全部项目</option>{#each inventory.projects as project}<option value={project.id}>{project.name}</option>{/each}</select></label><label>状态<select bind:value={keyStateFilter}><option value="">全部状态</option><option value="active">启用</option><option value="disabled">停用</option></select></label></div><div class="table-wrap"><table><thead><tr><th>名称</th><th>前缀</th><th>用户</th><th>项目</th><th>状态</th><th>操作</th></tr></thead><tbody>{#each keyPageRows as key}<tr><td>{key.name}</td><td><code>{key.key_prefix}</code></td><td>{subjectLabel(key.subject_id)}</td><td>{projectLabel(key.project_id)}</td><td><StateBadge value={key.state} /></td><td><button class="secondary" type="button" onclick={() => setKeyState(key.id, key.state === 'active' ? 'disabled' : 'active')}>{key.state === 'active' ? '禁用' : '启用'}</button></td></tr>{:else}<tr><td colspan="6" class="empty">没有匹配的密钥。</td></tr>{/each}</tbody></table></div><Pagination total={keyRows.length} page={keyPage} size={PAGE_SIZE.defaultList} onPage={(page) => (keyPage = page)} /></section>
				{:else if active === 'teams'}
					<PageTitle title={'权限组'} subtitle={'自助注册用户会继承其所有启用权限组的模型访问权限。'} />
					<div class="split">
						<section class="panel"><h2>创建权限组</h2><div class="form-grid"><label>名称<input bind:value={teamForm.name} /></label><label>备注<input bind:value={teamForm.notes} /></label><button type="button" onclick={createTeam}>创建权限组</button></div></section>
						<section class="panel"><h2>把用户加入权限组</h2><div class="form-grid"><label>搜索用户<input bind:value={teamSubjectSearch} placeholder="输入姓名或工号" /></label><label>权限组<select bind:value={teamMembershipForm.team_id}><option value="">权限组</option>{#each inventory.teams as team}<option value={team.id}>{team.name}</option>{/each}</select></label><label>用户<select bind:value={teamMembershipForm.subject_id}><option value="">用户</option>{#each subjectOptions(teamSubjectSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label><label>角色<select bind:value={teamMembershipForm.role}>{#each managedRoles as role}<option value={role.value}>{role.label}</option>{/each}</select></label><button type="button" onclick={createTeamMembership}>添加成员</button></div></section>
					</div>
					<section class="panel"><h2>给权限组授权模型</h2><div class="form-grid"><label>模型<select bind:value={modelTeamGrantForm.model_alias_id}><option value="">模型</option>{#each inventory.models as model}<option value={model.id}>{model.alias}</option>{/each}</select></label><label>权限组<select bind:value={modelTeamGrantForm.team_id}><option value="">权限组</option>{#each inventory.teams as team}<option value={team.id}>{team.name}</option>{/each}</select></label><button type="button" onclick={createModelTeamGrant}>授权模型</button></div></section>
					<section class="panel"><h2>权限组</h2><div class="table-wrap"><table><thead><tr><th>名称</th><th>状态</th><th>内置</th><th>备注</th><th>操作</th></tr></thead><tbody>{#each inventory.teams as team}<tr><td>{team.name}<br /><span class="muted">{short(team.id)}</span></td><td><StateBadge value={team.state} /></td><td><StateBadge value={team.is_builtin} tone="accent" /></td><td>{team.notes}</td><td><button class="secondary" type="button" onclick={() => patchTeam(team.id, { state: team.state === 'active' ? 'disabled' : 'active' })}>{team.state === 'active' ? '禁用' : '启用'}</button></td></tr>{/each}</tbody></table></div></section>
					<section class="panel"><h2>成员关系</h2><div class="form-grid"><label>权限组<select bind:value={teamMembershipTeamFilter}><option value="">全部权限组</option>{#each inventory.teams as team}<option value={team.id}>{team.name}</option>{/each}</select></label><label>搜索用户<input bind:value={teamMembershipSubjectSearch} placeholder="姓名或工号" /></label><label>角色<input bind:value={teamMembershipRoleFilter} placeholder="member" /></label><label>状态<select bind:value={teamMembershipStateFilter}><option value="">全部状态</option><option value="active">启用</option><option value="disabled">停用</option></select></label></div><div class="table-wrap"><table><thead><tr><th>权限组</th><th>用户</th><th>角色</th><th>状态</th><th>操作</th></tr></thead><tbody>{#each teamMembershipPageRows as membership}<tr><td>{teamLabel(membership.team_id)}</td><td>{subjectLabel(membership.subject_id)}</td><td>{membership.role}</td><td><StateBadge value={membership.state} /></td><td><button class="secondary" type="button" onclick={() => setTeamMembershipState(membership.id, membership.state === 'active' ? 'disabled' : 'active')}>{membership.state === 'active' ? '禁用' : '启用'}</button></td></tr>{:else}<tr><td colspan="5" class="empty">没有匹配的成员关系。</td></tr>{/each}</tbody></table></div><Pagination total={teamMembershipRows.length} page={teamMembershipPage} size={PAGE_SIZE.defaultList} onPage={(page) => (teamMembershipPage = page)} /></section>
					<section class="panel"><h2>模型授权</h2><div class="table-wrap"><table><thead><tr><th>模型</th><th>权限组</th><th>状态</th><th>操作</th></tr></thead><tbody>{#each inventory.modelTeamGrants as grant}<tr><td>{modelLabel(grant.model_alias_id)}</td><td>{teamLabel(grant.team_id)}</td><td><StateBadge value={grant.state} /></td><td><button class="secondary" type="button" onclick={() => setModelTeamGrantState(grant.id, grant.state === 'active' ? 'disabled' : 'active')}>{grant.state === 'active' ? '禁用' : '启用'}</button></td></tr>{/each}</tbody></table></div></section>
				{:else if active === 'skill-market'}
					<SkillMarketSection client={api} teams={inventory.teams} />
				{:else if active === 'mcp-market'}
					<McpMarketSection client={api} teams={inventory.teams} />
				{:else if active === 'entitlements'}
					<PageTitle title={'旧授权'} subtitle={'给项目、用户或单个网关密钥授予模型访问权限。'} />
					<section class="panel"><h2>创建授权</h2><div class="form-grid"><label>模型<select bind:value={entitlementForm.model_alias_id}><option value="">模型</option>{#each inventory.models as model}<option value={model.id}>{model.alias}</option>{/each}</select></label><label>范围<select bind:value={entitlementForm.scope} onchange={() => (entitlementForm.scope_id = '')}><option value="project">项目</option><option value="subject">用户</option><option value="key">密钥</option></select></label>{#if entitlementForm.scope === 'subject'}<label>搜索用户<input bind:value={entitlementSubjectSearch} placeholder="输入姓名或工号" /></label>{/if}<label>授权对象<select bind:value={entitlementForm.scope_id}><option value="">对象</option>{#each scopeOptions(entitlementForm.scope, entitlementSubjectSearch) as option}<option value={option.id}>{option.label}</option>{/each}</select></label><button type="button" onclick={createEntitlement}>授权访问</button></div></section>
					<section class="panel"><h2>授权</h2><div class="table-wrap"><table><thead><tr><th>模型</th><th>范围</th><th>状态</th><th>操作</th></tr></thead><tbody>{#each inventory.entitlements as entitlement}<tr><td>{modelLabel(entitlement.model_alias_id)}</td><td>{entitlement.project_id ? `项目: ${projectLabel(entitlement.project_id)}` : entitlement.subject_id ? `用户: ${subjectLabel(entitlement.subject_id)}` : `密钥: ${keyLabel(entitlement.gateway_key_id)}`}</td><td><StateBadge value={entitlement.state} /></td><td><button class="secondary" type="button" onclick={() => setEntitlementState(entitlement.id, entitlement.state === 'active' ? 'disabled' : 'active')}>{entitlement.state === 'active' ? '禁用' : '启用'}</button></td></tr>{/each}</tbody></table></div></section>
				{:else if active === 'rate'}
					<PageTitle title={'限流策略'} subtitle={'基于数据库配置的每分钟请求数和并发限制。'} />
					<section class="panel"><h2>创建限流策略</h2><p>实际生效限制会取密钥、用户、项目和环境默认值中的最小启用策略。</p><div class="form-grid"><label>范围<select bind:value={rateForm.scope} onchange={() => (rateForm.scope_id = '')}><option value="key">密钥</option><option value="subject">用户</option><option value="project">项目</option></select></label>{#if rateForm.scope === 'subject'}<label>搜索用户<input bind:value={rateSubjectSearch} placeholder="输入姓名或工号" /></label>{/if}<label>对象<select bind:value={rateForm.scope_id}><option value="">对象</option>{#each scopeOptions(rateForm.scope, rateSubjectSearch) as option}<option value={option.id}>{option.label}</option>{/each}</select></label><label>每分钟请求数<input type="number" min="0" bind:value={rateForm.requests_per_minute} /></label><label>并发限制<input type="number" min="0" bind:value={rateForm.concurrency_limit} /></label><button type="button" onclick={createRatePolicy}>创建策略</button></div></section>
					<section class="panel"><h2>策略</h2><div class="table-wrap"><table><thead><tr><th>范围</th><th>对象</th><th>RPM</th><th>并发</th><th>状态</th><th>操作</th></tr></thead><tbody>{#each inventory.ratePolicies as policy}<tr><td>{scopeLabel(policy.scope)}</td><td>{policy.scope === 'subject' ? subjectLabel(policy.scope_id) : policy.scope === 'project' ? projectLabel(policy.scope_id) : keyLabel(policy.scope_id)}</td><td>{policy.requests_per_minute ?? '继承'}</td><td>{policy.concurrency_limit ?? '继承'}</td><td><StateBadge value={policy.state} /></td><td><button class="secondary" type="button" onclick={() => setRateState(policy.id, policy.state === 'active' ? 'disabled' : 'active')}>{policy.state === 'active' ? '禁用' : '启用'}</button></td></tr>{/each}</tbody></table></div></section>
				{:else if active === 'usage'}
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
					<section class="panel"><div class="actions"><button class="secondary" type="button" onclick={() => setUsageRange(1 / 24)}>最近 1 小时</button><button class="secondary" type="button" onclick={() => setUsageRange(1)}>最近 1 天</button><button class="secondary" type="button" onclick={() => setUsageRange(7)}>最近 1 周</button><button class="secondary" type="button" onclick={() => setUsageRange(30)}>最近 1 月</button></div><div class="form-grid"><label>开始时间<input type="datetime-local" bind:value={usageStart} /></label><label>结束时间<input type="datetime-local" bind:value={usageEnd} /></label><label>时间粒度<select bind:value={analyticsBucket}><option value="minute">分钟</option><option value="hour">小时</option><option value="day">天</option></select></label><label>分析维度<select bind:value={analyticsDimension}><option value="model">模型</option><option value="subject">用户</option><option value="project">项目</option><option value="endpoint">协议</option><option value="outcome">结果</option><option value="streaming">流式</option></select></label><label>模型筛选<select bind:value={modelFilter}><option value="">全部</option>{#each inventory.models as model}<option value={model.alias}>{model.alias}</option>{/each}</select></label><label>搜索用户<input bind:value={usageSubjectSearch} placeholder="输入姓名或工号" /></label><label>用户筛选<select bind:value={subjectFilter}><option value="">全部</option>{#each subjectOptions(usageSubjectSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label><label>项目筛选<select bind:value={projectFilter}><option value="">全部</option>{#each inventory.projects as project}<option value={project.id}>{project.name}</option>{/each}</select></label><button type="button" onclick={refreshUsageAnalytics} disabled={loading}>{loading ? '查询中' : '查询'}</button></div></section>
					<section class="panel">
					</section>
					<div class="grid"><div class="metric"><span>请求数</span><strong>{totals.requests}</strong></div><div class="metric"><span>总 token</span><strong>{totals.total}</strong></div><div class="metric"><span>成功</span><strong>{totals.success}</strong></div><div class="metric"><span>失败</span><strong>{totals.failure}</strong></div><div class="metric"><span>平均延迟</span><strong>{msLabel(analyticsPerformance.latencyWeight ? analyticsPerformance.latencyTotal / analyticsPerformance.latencyWeight : null)}</strong></div><div class="metric"><span>平均 TTFT</span><strong>{msLabel(analyticsPerformance.ttftWeight ? analyticsPerformance.ttftTotal / analyticsPerformance.ttftWeight : null)}</strong></div><div class="metric"><span>Retry / Fallback</span><strong>{analyticsPerformance.retry} / {analyticsPerformance.fallback}</strong></div><div class="metric"><span>vLLM 指标覆盖</span><strong>{analyticsPerformance.vllmObserved} / {analyticsPerformance.requests}</strong></div></div>
					<section class="panel"><h2>最近 5 个时间桶</h2><AnalyticsBucketTable rows={visibleAnalyticsBuckets} maxTokens={analyticsMaxTokens} /></section>
					<section class="panel"><h2>Top 5 Drilldown</h2><AnalyticsDrilldownTable rows={visibleAnalyticsDrilldown} /></section>
					<section class="panel"><h2>Top 5 汇总明细</h2><UsageTable rows={visibleUsageRows} subjectLabel={subjectLabel} projectLabel={projectLabel} /></section>
				{:else if active === 'ranking'}
					<PageTitle title={'排行榜'} subtitle={'按时间范围统计 token 用量最高的用户。'} />
					<section class="panel"><div class="actions"><button class="secondary" type="button" onclick={() => setUsageRange(1 / 24)}>最近 1 小时</button><button class="secondary" type="button" onclick={() => setUsageRange(1)}>最近 1 天</button><button class="secondary" type="button" onclick={() => setUsageRange(7)}>最近 1 周</button><button class="secondary" type="button" onclick={() => setUsageRange(30)}>最近 1 月</button></div><div class="form-grid"><label>开始时间<input type="datetime-local" bind:value={usageStart} /></label><label>结束时间<input type="datetime-local" bind:value={usageEnd} /></label><label>模型筛选<select bind:value={rankingModel}><option value="">全部</option>{#each inventory.models as model}<option value={model.alias}>{model.alias}</option>{/each}</select></label><label>Top N<input type="number" bind:value={rankingLimit} min="1" max="100" /></label><button type="button" onclick={refreshUsageRanking} disabled={loading}>{loading ? '查询中' : '查询'}</button></div></section>
					<section class="panel">
					</section>
					<section class="panel"><div class="table-wrap"><table><thead><tr><th>#</th><th>用户 / Subject</th><th>请求数</th><th>输入 token</th><th>输出 token</th><th>总 token</th></tr></thead><tbody>{#each rankingPageRows as row, i}<tr><td>{(rankingPage - 1) * PAGE_SIZE.ranking + i + 1}</td><td>{row.subject_name} / {row.login_username ?? row.subject_id}</td><td>{row.request_count}</td><td>{row.prompt_tokens}</td><td>{row.completion_tokens}</td><td>{row.total_tokens}</td></tr>{:else}<tr><td colspan="6" class="empty">暂无用量数据。</td></tr>{/each}</tbody></table></div><Pagination total={rankingRows.length} page={rankingPage} size={PAGE_SIZE.ranking} onPage={(page) => (rankingPage = page)} /></section>
				{:else if active === 'audit'}
					<PageTitle title={'审计'} subtitle={'最近的权限变更和安全相关事件。'} />
					<section class="panel"><AuditTable rows={auditPageRows} onDetail={(event) => (auditDetail = event)} /><Pagination total={auditRows.length} page={auditPage} size={PAGE_SIZE.audit} onPage={(page) => (auditPage = page)} /></section>
				{:else if active === 'diagnostics'}
					<PageTitle title={'诊断'} subtitle={'运行时依赖和上游健康检查。'} />
					<div class="grid"><div class="metric"><span>Postgres</span><strong>{ready?.checks.postgres ? '正常' : '异常'}</strong></div><div class="metric"><span>Redis</span><strong>{ready?.checks.redis ? '正常' : '异常'}</strong></div><div class="metric"><span>环境</span><strong>{diagnostics?.environment}</strong></div></div>
					<section class="panel">
						<h2>健康巡检</h2>
						<p class="muted">自动探测每个活跃上游的 <code>/models</code>，故障时在 Redis 标记 UNHEALTHY 并从路由排除。关闭后 sidecar 仍运行但跳过探测，已有标记靠 TTL 自动过期恢复。</p>
						<div style="display:flex;align-items:center;gap:0.75rem;flex-wrap:wrap">
							<strong>自动巡检：{healthCheckConfig?.enabled ? '已开启' : '已关闭'}</strong>
							{#if healthCheckConfig}
								<span class="muted">来源：{healthCheckConfig.source === 'redis_override' ? '运行时覆盖' : '环境变量默认'}</span>
								<button class="secondary" type="button" disabled={healthCheckToggling} onclick={toggleHealthCheck}>{healthCheckConfig.enabled ? '关闭巡检' : '开启巡检'}</button>
							{/if}
						</div>
					</section>
					<UpstreamTable rows={inventory.upstreams} healthResults={healthResults} modelLabel={modelLabel} onCheck={checkUpstream} onState={setUpstreamState} onPatch={patchUpstream} onDelete={deleteUpstream} onError={(m) => (pageError = m)} />
				{/if}
			</section>
		</main>
	</div>
{/if}

{#if auditDetail}
	<div class="modal-backdrop" role="presentation">
		<section class="modal" aria-label="审计详情">
			<header><h2>{auditDetail.action}</h2><p>{auditDetail.resource_type} · {auditDetail.created_at}</p></header>
			<JsonViewer value={auditDetail} />
			<footer><button class="secondary" type="button" onclick={() => (auditDetail = null)}>关闭</button></footer>
		</section>
	</div>
{/if}

{#if cidrEditorModel}
	<div class="modal-backdrop" role="presentation">
		<section class="modal" aria-label="编辑 CIDR 白名单">
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

<SecretOnceDialog secret={plaintextKey} onClose={() => (plaintextKey = '')} />


