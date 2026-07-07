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
		GatewayKeyCreateResponse,
		Inventory,
		IPPolicyMode,
		ManagedRankingRow,
		OwnUsageSummary,
		ProjectMembership,
		ReadyStatus,
		ResourceState,
		Subject,
		SubjectType,
		TeamMembership
	} from '$lib/api/types';
	import { createSessionStore } from '$lib/state/session.svelte';
	import { createInventoryStore } from '$lib/state/inventory.svelte';
	import StateBadge from '$lib/components/StateBadge.svelte';
	import JsonViewer from '$lib/components/JsonViewer.svelte';
	import CommandBlock from '$lib/components/CommandBlock.svelte';
	import SecretOnceDialog from '$lib/components/SecretOnceDialog.svelte';
	import PageTitle from '$lib/components/PageTitle.svelte';
	import CopyValue from '$lib/components/CopyValue.svelte';
	import AuthScreen from '$lib/components/AuthScreen.svelte';
	import Modal from '$lib/components/Modal.svelte';
	import OwnedDashboard from '$lib/components/OwnedDashboard.svelte';
	import SkillMarketSection from '$lib/components/SkillMarketSection.svelte';
	import McpMarketSection from '$lib/components/McpMarketSection.svelte';
	import AdminUsage from '$lib/components/admin/Usage.svelte';
	import AdminModels from '$lib/components/admin/Models.svelte';
	import AdminSubjects from '$lib/components/admin/Subjects.svelte';
	import AdminProjects from '$lib/components/admin/Projects.svelte';
	import AdminTeams from '$lib/components/admin/Teams.svelte';
	import AdminUpstreams from '$lib/components/admin/Upstreams.svelte';
	import AdminDiagnostics from '$lib/components/admin/Diagnostics.svelte';
	import AdminKeys from '$lib/components/admin/Keys.svelte';
	import AdminEntitlements from '$lib/components/admin/Entitlements.svelte';
	import AdminRate from '$lib/components/admin/Rate.svelte';
	import AdminRanking from '$lib/components/admin/Ranking.svelte';
	import AdminAudit from '$lib/components/admin/Audit.svelte';
	import { parseCidrList, parseJsonObject, validateCidrList, validateHttpUrl } from '$lib/validators';

	import {
		PAGE_SIZE,
		sections,
		navGroups,
		employeeIdPattern,
		subjectDisplay,
		matchNeedle,
		pageRows,
		filteredSubjects as filteredSubjectsConfig,
		subjectOptions as subjectOptionsConfig,
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
	const session = createSessionStore();
	// Inventory/realtime/health store. Bound to the live session API client
	// and token so the SSE consumer and fetches always use the fresh header.
	const inventory = createInventoryStore(() => session.api, () => session.sessionToken);
	let profile = $state<AuthProfile | null>(null);
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

	// —— Promise-based confirm dialog (replaces window.confirm for delete paths) ——
	let confirmOpen = $state(false);
	let confirmTitle = $state('请确认');
	let confirmMessage = $state('');
	let confirmDanger = $state(true);
	let confirmConfirmLabel = $state('确认');
	let confirmResolve = $state<((ok: boolean) => void) | null>(null);

	/** Show a Modal-backed confirm; resolves true on confirm, false on cancel. */
	function askConfirm(message: string, opts?: { title?: string; confirmLabel?: string; danger?: boolean }): Promise<boolean> {
		confirmMessage = message;
		confirmTitle = opts?.title ?? '请确认';
		confirmConfirmLabel = opts?.confirmLabel ?? '确认';
		confirmDanger = opts?.danger ?? true;
		confirmOpen = true;
		return new Promise<boolean>((resolve) => {
			confirmResolve = resolve;
		});
	}

	function resolveConfirm(ok: boolean) {
		const resolve = confirmResolve;
		confirmResolve = null;
		confirmOpen = false;
		resolve?.(ok);
	}

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
		inventory.inventory.usage.filter((row) => {
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
		subjects: inventory.inventory.subjects,
		managedSubjectCandidates,
		selfSubjectId: profile?.subject.id,
		selfSubject: profile?.subject ?? null,
		projects: inventory.inventory.projects,
		keys: inventory.inventory.keys,
		models: inventory.inventory.models,
		teams: inventory.inventory.teams
	});

	const totals = $derived(
		{
			requests: Number(inventory.inventory.usageTotals?.request_count ?? 0),
			prompt: Number(inventory.inventory.usageTotals?.prompt_tokens ?? 0),
			completion: Number(inventory.inventory.usageTotals?.completion_tokens ?? 0),
			total: Number(inventory.inventory.usageTotals?.total_tokens ?? 0),
			success: Number(inventory.inventory.usageTotals?.success_count ?? 0),
			failure: Number(inventory.inventory.usageTotals?.failure_count ?? 0)
		}
	);
	const visibleUsageRows = $derived(
		usageRows.toSorted((a, b) => Number(b.total_tokens ?? 0) - Number(a.total_tokens ?? 0)).slice(0, PAGE_SIZE.usagePreview)
	);
	const visibleAnalyticsBuckets = $derived(inventory.inventory.analyticsBuckets.slice(0, PAGE_SIZE.usagePreview));
	const visibleAnalyticsDrilldown = $derived(inventory.inventory.analyticsDrilldown.slice(0, PAGE_SIZE.usagePreview));
	const realtimeRows = $derived.by(() => {
		const live = inventory.realtime?.upstreams ?? [];
		if (!inventory.realtimeLocked) return live;
		// 锁定:全部活动配置端点按名排序,合并 realtime 指标(无数据则填占位行)
		return inventory.inventory.upstreams
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
	const realtimeUpdatedLabel = $derived(inventory.realtime ? new Date(inventory.realtime.generated_at).toLocaleTimeString() : '无');
	const analyticsMaxTokens = $derived(
		Math.max(1, ...visibleAnalyticsBuckets.map((row) => Number(row.total_tokens ?? 0)))
	);
	const subjectRows = $derived(filteredSubjectsConfig(subjectSearch, inventory.inventory.subjects).toSorted((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN')));
	const dropdownProjects = $derived(inventory.inventory.projects.filter((p) => !p.name.startsWith('user-')));
	const projectRows = $derived(
		inventory.inventory.projects
			.filter((project) => matchNeedle(projectSearch, [project.name, project.notes ?? '', subjectLabel(project.owner_subject_id)]))
			.toSorted((a, b) => b.created_at.localeCompare(a.created_at))
	);
	const keyRows = $derived(
		inventory.inventory.keys
			.filter((key) => {
				if (keyProjectFilter && key.project_id !== keyProjectFilter) return false;
				if (keyStateFilter && key.state !== keyStateFilter) return false;
				if (!matchNeedle(keyListSubjectSearch, [subjectLabel(key.subject_id), key.name, key.key_prefix])) return false;
				return true;
			})
			.toSorted((a, b) => b.created_at.localeCompare(a.created_at))
	);
	const teamMembershipRows = $derived(
		inventory.inventory.teamMemberships
			.filter((membership) => {
				if (teamMembershipTeamFilter && membership.team_id !== teamMembershipTeamFilter) return false;
				if (teamMembershipStateFilter && membership.state !== teamMembershipStateFilter) return false;
				if (teamMembershipRoleFilter && !membership.role.toLowerCase().includes(teamMembershipRoleFilter.trim().toLowerCase())) return false;
				if (!matchNeedle(teamMembershipSubjectSearch, [subjectLabel(membership.subject_id)])) return false;
				return true;
			})
			.toSorted((a, b) => b.created_at.localeCompare(a.created_at))
	);
	const rankingRows = $derived(inventory.inventory.ranking.slice(0, PAGE_SIZE.ranking));
	const auditRows = $derived(inventory.inventory.audit.toSorted((a, b) => b.created_at.localeCompare(a.created_at)));
	const subjectPageRows = $derived(pageRows(subjectRows, subjectPage, PAGE_SIZE.defaultList));
	const projectPageRows = $derived(pageRows(projectRows, projectPage, PAGE_SIZE.defaultList));
	const keyPageRows = $derived(pageRows(keyRows, keyPage, PAGE_SIZE.defaultList));
	const teamMembershipPageRows = $derived(pageRows(teamMembershipRows, teamMembershipPage, PAGE_SIZE.defaultList));
	const rankingPageRows = $derived(pageRows(rankingRows, rankingPage, PAGE_SIZE.ranking));
	const auditPageRows = $derived(pageRows(auditRows, auditPage, PAGE_SIZE.audit));
	const analyticsPerformance = $derived(
		{
			requests: Number(inventory.inventory.usageTotals?.request_count ?? 0),
			retry: Number(inventory.inventory.usageTotals?.retry_count ?? 0),
			fallback: Number(inventory.inventory.usageTotals?.fallback_count ?? 0),
			vllmObserved: Number(inventory.inventory.usageTotals?.vllm_metrics_count ?? 0),
			latencyTotal: Number(inventory.inventory.usageTotals?.avg_latency_ms ?? 0),
			latencyWeight: inventory.inventory.usageTotals?.avg_latency_ms == null ? 0 : 1,
			ttftTotal: Number(inventory.inventory.usageTotals?.avg_ttft_ms ?? 0),
			ttftWeight: inventory.inventory.usageTotals?.avg_ttft_ms == null ? 0 : 1
		}
	);

	onMount(() => {
		const range = defaultUsageRange();
		usageStart = range.start;
		usageEnd = range.end;
		ownUsageStart = range.start;
		ownUsageEnd = range.end;
		gatewayBaseUrl = inferGatewayBaseUrl();
		session.rememberSession = Boolean(session.sessionToken);
		void session.refreshReady();
		if (session.sessionToken) void loadProfile(true);
	});

	onDestroy(() => {
		inventory.stopRealtimeStream();
	});

	// After a successful auth, hydrate page-level state (profile, diagnostics,
	// realtime, inventory) based on admin vs. non-admin. Shared by login,
	// register and loadProfile.
	async function onAuthed(authProfile: AuthProfile, authedApi: AdminApiClient): Promise<void> {
		profile = authProfile;
		if (authProfile.subject.is_admin) {
			await inventory.fetchDiagnostics();
			await refreshAll();
			inventory.startRealtimeStream();
		} else {
			inventory.stopRealtimeStream();
			await refreshManagedRoles();
			await fetchOwnUsage();
		}
	}

	async function loginAccount(fromStorage = false) {
		await session.login(loginForm, {
			remember: session.rememberSession,
			fromStorage,
			onAuthed
		});
	}

	async function registerAccount() {
		const result = await session.register(registerForm, {
			remember: session.rememberSession,
			onAuthed
		});
		if (result.ok) registerForm = { username: '', full_name: '', password: '' };
	}

	async function loadProfile(fromStorage = false) {
		await session.loadProfile({ fromStorage, remember: session.rememberSession, onAuthed });
	}

	function disconnect() {
		inventory.stopRealtimeStream();
		session.logout();
		profile = null;
		copiedItem = '';
		inventory.resetInventory();
	}

	async function refreshAll() {
		await run(async () => {
			await session.refreshReady();
			if (!isAdmin) {
				profile = await session.api.get<AuthProfile>('/auth/me');
				await refreshManagedRoles();
				await fetchOwnUsage();
				return;
			}
			await inventory.refreshInventory();
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
				session.api.get<Inventory['usageTotals']>('/admin/usage/totals', analyticsParams),
				session.api.get<Inventory['usage']>('/admin/usage/summary', {
					...analyticsParams,
					limit: PAGE_SIZE.usagePreview
				}),
				session.api.get<Inventory['analyticsBuckets']>('/admin/analytics/time-buckets', {
					...analyticsParams,
					bucket: analyticsBucket,
					limit: PAGE_SIZE.usagePreview
				}),
				session.api.get<Inventory['analyticsDrilldown']>('/admin/analytics/drilldown', {
					...analyticsParams,
					dimension: analyticsDimension,
					limit: PAGE_SIZE.usagePreview
				})
			]);
			inventory.patchInventory({ usageTotals, usage, analyticsBuckets: buckets, analyticsDrilldown: drilldown });
		});
	}

	async function refreshUsageRanking() {
		await run(async () => {
			const ranking = await session.api.get<Inventory['ranking']>('/admin/usage/ranking', {
				start: usageStart,
				end: usageEnd,
				model: rankingModel,
				limit: rankingLimit
			});
			inventory.patchInventory({ ranking });
		});
	}

	async function createSubject() {
		if (subjectForm.login_username && !employeeIdPattern.test(subjectForm.login_username.trim())) {
			session.pageError = '工号必须是 1 个字母加 8 位数字，例如 l00014624。';
			return;
		}
		await run(async () => {
			await session.api.post('/admin/subjects', clean({ ...subjectForm }));
			subjectForm = { name: '', login_username: '', password: '', type: 'user', notes: '' };
			await refreshAll();
		});
	}

	async function patchSubject(id: string, patch: Record<string, unknown>) {
		await run(async () => {
			await session.api.patch(`/admin/subjects/${id}`, patch);
			await refreshAll();
		});
	}

	async function setSubjectState(id: string, state: ResourceState) {
		await run(async () => {
			await session.api.patch(`/admin/subjects/${id}/state`, { state });
			await refreshAll();
		});
	}

	async function resetSubjectPassword() {
		if (!subjectPasswordForm.subject_id || subjectPasswordForm.new_password.length < 8) {
			session.pageError = '请选择用户，新密码至少 8 个字符。';
			return;
		}
		await run(async () => {
			await session.api.patch(`/admin/subjects/${subjectPasswordForm.subject_id}/password`, {
				new_password: subjectPasswordForm.new_password
			});
			subjectPasswordForm = { subject_id: '', new_password: '' };
		});
	}

	async function deleteSubject(subject: Inventory['subjects'][number]) {
		if (!(await askConfirm(`确认删除用户 ${subject.name}（${subject.login_username ?? '无工号'}）？`, { title: '删除用户', confirmLabel: '删除' }))) return;
		await run(async () => {
			await session.api.delete(`/admin/subjects/${subject.id}`);
			await refreshAll();
		});
	}

	async function createProject() {
		await run(async () => {
			await session.api.post('/admin/projects', clean({ ...projectForm }));
			projectForm = { name: '', owner_subject_id: '', notes: '' };
			await refreshAll();
		});
	}

	async function patchProject(id: string, patch: Record<string, unknown>) {
		await run(async () => {
			await session.api.patch(`/admin/projects/${id}`, clean(patch));
			await refreshAll();
		});
	}

	async function createMembership() {
		await run(async () => {
			await session.api.post('/admin/project-memberships', clean({ ...membershipForm }));
			membershipForm = { project_id: '', subject_id: '', role: 'member' };
			await refreshAll();
		});
	}

	async function issueKey() {
		await run(async () => {
			const response = await session.api.post<GatewayKeyCreateResponse>('/admin/gateway-keys', clean({ ...keyForm }));
			session.plaintextKey = response.plaintext_key;
			keyForm = { subject_id: '', project_id: '', name: '' };
			await refreshAll();
		});
	}

	async function issueOwnKey() {
		await run(async () => {
			const response = await session.api.post<GatewayKeyCreateResponse>('/auth/keys', clean({ ...ownKeyForm }));
			session.plaintextKey = response.plaintext_key;
			ownKeyForm = { name: '个人密钥' };
			profile = await session.api.get<AuthProfile>('/auth/me');
			await fetchOwnUsage();
		});
	}

	async function setOwnKeyState(key: { id: string; state: string }, newState: 'active' | 'disabled') {
		await run(async () => {
			await session.api.patch(`/auth/keys/${key.id}/state`, { state: newState });
			profile = await session.api.get<AuthProfile>('/auth/me');
		});
	}

	async function changeOwnPassword() {
		if (!ownPasswordForm.current_password || ownPasswordForm.new_password.length < 8) {
			session.pageError = '请输入当前密码，新密码至少 8 个字符。';
			return;
		}
		await run(async () => {
			await session.api.patch('/auth/password', ownPasswordForm);
			ownPasswordForm = { current_password: '', new_password: '' };
		});
	}

	async function submitRealName() {
		realNameError = '';
		if (!realNameForm.full_name.trim()) {
			realNameError = '请填写真实姓名。';
			return;
		}
		session.loading = true;
		session.pageError = '';
		try {
			profile = await session.api.patch<AuthProfile>('/auth/profile', {
				full_name: realNameForm.full_name.trim()
			});
			realNameForm = { full_name: '' };
		} catch (error) {
			realNameError = errorMessage(error);
		} finally {
			session.loading = false;
		}
	}

	async function setKeyState(id: string, state: ResourceState) {
		await run(async () => {
			await session.api.patch(`/admin/gateway-keys/${id}/state`, { state });
			await refreshAll();
		});
	}

	async function createModel() {
		const cidrCheck = modelForm.ip_policy_mode === 'allowlist' ? validateCidrList(modelForm.ip_allowlist_cidrs) : { ok: true };
		if (!cidrCheck.ok) {
			session.pageError = cidrCheck.message ?? 'CIDR 列表不合法';
			return;
		}
		await run(async () => {
			await session.api.post(
				'/admin/model-aliases',
				clean({
					alias: modelForm.alias,
					upstream_model_name: modelForm.upstream_model_name,
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
			await session.api.patch(`/admin/model-aliases/${id}`, patch);
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
			session.pageError = cidrCheck.message ?? 'CIDR 列表不合法';
			return;
		}
		await patchModel(cidrEditorModel.id, {
			ip_policy_mode: 'allowlist',
			ip_allowlist_cidrs: parseCidrList(trimmed)
		});
		cidrEditorModel = null;
	}

	async function deleteModel(model: Inventory['models'][number]) {
		if (!(await askConfirm(`确认删除模型别名 ${model.alias}？`, { title: '删除模型别名', confirmLabel: '删除' }))) return;
		await run(async () => {
			try {
				await session.api.delete(`/admin/model-aliases/${model.id}`);
			} catch (error) {
				if (isModelUpstreamConflict(error)) {
					const upstreamCount = Number((error.detail as { upstream_count?: number }).upstream_count ?? 0);
					if (await askConfirm(`这个模型还有 ${upstreamCount} 个上游端点依赖。是否一起删除这些上游依赖？`, { title: '级联删除上游', confirmLabel: '一起删除' })) {
						await session.api.delete(`/admin/model-aliases/${model.id}`, { cascade_upstreams: true });
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
		if (!(await askConfirm(`确认删除上游端点 ${upstream.name}？`, { title: '删除上游端点', confirmLabel: '删除' }))) return;
		await run(async () => {
			await session.api.delete(`/admin/upstreams/${upstream.id}`);
			await refreshAll();
		});
	}

	async function createUpstream() {
		const urlCheck = validateHttpUrl(upstreamForm.base_url, '上游地址');
		if (!urlCheck.ok) {
			session.pageError = urlCheck.message ?? '上游地址不合法';
			return;
		}
		if (upstreamForm.metrics_url.trim()) {
			const metricsUrlCheck = validateHttpUrl(upstreamForm.metrics_url, 'Metrics URL');
			if (!metricsUrlCheck.ok) {
				session.pageError = metricsUrlCheck.message ?? 'Metrics URL 不合法';
				return;
			}
		}
		if (!upstreamForm.health_path.startsWith('/')) {
			session.pageError = '健康检查路径必须以 / 开头。';
			return;
		}
		await run(async () => {
			await session.api.post(
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
			await session.api.patch(`/admin/upstreams/${id}`, { state });
			await refreshAll();
		});
	}

	async function patchUpstream(id: string, patch: Record<string, unknown>) {
		await run(async () => {
			await session.api.patch(`/admin/upstreams/${id}`, clean(patch));
			await refreshAll();
		});
	}

	async function checkUpstream(id: string) {
		await inventory.checkUpstream(id);
	}

	async function createEntitlement() {
		await run(async () => {
			await session.api.post(
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
			await session.api.patch(`/admin/model-entitlements/${id}/state`, { state });
			await refreshAll();
		});
	}

	async function createTeam() {
		await run(async () => {
			await session.api.post('/admin/teams', clean({ ...teamForm }));
			teamForm = { name: '', notes: '' };
			await refreshAll();
		});
	}

	async function patchTeam(id: string, patch: Record<string, unknown>) {
		await run(async () => {
			await session.api.patch(`/admin/teams/${id}`, clean(patch));
			await refreshAll();
		});
	}

	async function createTeamMembership() {
		await run(async () => {
			await session.api.post('/admin/team-memberships', clean({ ...teamMembershipForm }));
			teamMembershipForm = { team_id: '', subject_id: '', role: 'member' };
			await refreshAll();
		});
	}

	async function setTeamMembershipState(id: string, state: ResourceState) {
		await run(async () => {
			await session.api.patch(`/admin/team-memberships/${id}/state`, { state });
			await refreshAll();
		});
	}

	async function createModelTeamGrant() {
		await run(async () => {
			await session.api.post('/admin/model-team-grants', clean({ ...modelTeamGrantForm }));
			modelTeamGrantForm = { model_alias_id: '', team_id: '' };
			await refreshAll();
		});
	}

	async function setModelTeamGrantState(id: string, state: ResourceState) {
		await run(async () => {
			await session.api.patch(`/admin/model-team-grants/${id}/state`, { state });
			await refreshAll();
		});
	}

	async function createRatePolicy() {
		await run(async () => {
			await session.api.post(
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
			await session.api.patch(`/admin/rate-policies/${id}`, { state });
			await refreshAll();
		});
	}

	async function refreshOwnUsage() {
		await run(fetchOwnUsage);
	}

	async function fetchOwnUsage() {
		ownUsage = await session.api.get<OwnUsageSummary>('/auth/usage/summary', {
			start: ownUsageStart,
			end: ownUsageEnd
		});
	}

	async function refreshManagedSubjects() {
		await run(async () => {
			managedSubjectCandidates = await session.api.get<Subject[]>('/auth/managed/subjects', {
				q: managedSubjectSearch,
				limit: PAGE_SIZE.selectOptions
			});
		});
	}

	async function refreshManagedRoles() {
		if (!hasManagedResources) return;
		managedRoles = await session.api.get<{ value: string; label: string }[]>('/auth/managed/roles');
	}

	async function refreshManagedProjectMemberships(resourceId = managedProjectMemberForm.resource_id) {
		if (!resourceId) {
			managedProjectMemberships = [];
			return;
		}
		managedProjectMemberships = await session.api.get<ProjectMembership[]>('/auth/managed/project-memberships', {
			resource_id: resourceId
		});
	}

	async function refreshManagedTeamMemberships(resourceId = managedTeamMemberForm.resource_id) {
		if (!resourceId) {
			managedTeamMemberships = [];
			return;
		}
		managedTeamMemberships = await session.api.get<TeamMembership[]>('/auth/managed/team-memberships', {
			resource_id: resourceId
		});
	}

	async function refreshManagedUsage() {
		await run(async () => {
			managedUsage = await session.api.get<OwnUsageSummary>('/auth/managed/usage/summary', {
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
			const data = await session.api.get<{ ranking: ManagedRankingRow[] }>(
				'/auth/managed/usage/ranking',
				params
			);
			managedRanking = data.ranking;
		});
	}

	async function addManagedProjectMember() {
		if (!managedProjectMemberForm.resource_id || !managedProjectMemberForm.subject_id) {
			session.pageError = '请选择项目和用户。';
			return;
		}
		await run(async () => {
			await session.api.post('/auth/managed/project-memberships', {
				resource_id: managedProjectMemberForm.resource_id,
				subject_id: managedProjectMemberForm.subject_id,
				role: managedProjectMemberForm.role || 'member'
			});
			await refreshManagedProjectMemberships();
		});
	}

	async function removeManagedProjectMember(membership: ProjectMembership) {
		if (!(await askConfirm(`确认从项目中移除 ${membershipSubjectLabel(membership)}？`, { title: '移除项目成员', confirmLabel: '移除' }))) return;
		await run(async () => {
			await session.api.delete(`/auth/managed/project-memberships/${membership.id}`);
			await refreshManagedProjectMemberships();
		});
	}

	async function addManagedTeamMember() {
		if (!managedTeamMemberForm.resource_id || !managedTeamMemberForm.subject_id) {
			session.pageError = '请选择权限组和用户。';
			return;
		}
		await run(async () => {
			await session.api.post('/auth/managed/team-memberships', {
				resource_id: managedTeamMemberForm.resource_id,
				subject_id: managedTeamMemberForm.subject_id,
				role: managedTeamMemberForm.role || 'member'
			});
			await refreshManagedTeamMemberships();
		});
	}

	async function setManagedTeamMemberState(membership: TeamMembership, state: ResourceState) {
		await run(async () => {
			await session.api.patch(`/auth/managed/team-memberships/${membership.id}`, { state });
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
		session.loading = true;
		session.pageError = '';
		try {
			await fn();
		} catch (error) {
			session.pageError = errorMessage(error);
		} finally {
			session.loading = false;
		}
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
		return inventory.inventory.keys.map((item) => ({ id: item.id, label: `${item.name} (${item.key_prefix})` }));
	}

	function subjectOptions(query: string) {
		return subjectOptionsConfig(query, inventory.inventory.subjects);
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

{#if !session.connected}
	<AuthScreen
		ready={session.ready}
		bind:loginForm
		bind:registerForm
		bind:rememberSession={session.rememberSession}
		pageError={session.pageError}
		loading={session.loading}
		onLogin={loginAccount}
		onRegister={registerAccount}
		onRefreshReady={session.refreshReady}
	/>
{:else}
	<div class="app">
		<aside class="sidebar">
			<div class="brand">
				<strong>LLM Gateway</strong>
				<span>{inventory.diagnostics?.environment ?? '环境未知'}</span>
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
					<StateBadge value={session.ready?.ok ? 'ready' : 'not_ready'} tone={session.ready?.ok ? 'success' : 'danger'} />
					<span class="muted">Postgres {session.ready?.checks.postgres ? '正常' : '异常'} · Redis {session.ready?.checks.redis ? '正常' : '异常'}</span>
				</div>
				<div class="actions">
					<button class="secondary" type="button" onclick={refreshAll} disabled={session.loading}>{session.loading ? '处理中' : '刷新'}</button>
					<button class="secondary" type="button" onclick={disconnect}>退出登录</button>
				</div>
			</div>
			<section class="content">
				{#if session.pageError}<div class="error">{session.pageError}</div>{/if}

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
						loading={session.loading}
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
					<SkillMarketSection client={session.api} teams={marketTeams} />
				{:else if active === 'mcp-market'}
					<PageTitle title={'MCP 市场'} subtitle={'发布和管理你的 MCP 连接配置,并授权给权限组。'} />
					<McpMarketSection client={session.api} teams={marketTeams} />
				{:else if active === 'models'}
					<AdminModels
						models={inventory.inventory.models}
						bind:modelForm
						onCreate={createModel}
						onEditCidrs={editModelCidrs}
						onPatch={patchModel}
						onDelete={deleteModel}
					/>
				{:else if active === 'upstreams'}
					<AdminUpstreams
						upstreams={inventory.inventory.upstreams}
						models={inventory.inventory.models}
						healthResults={inventory.healthResults}
						{modelLabel}
						bind:upstreamForm
						onCreate={createUpstream}
						onCheck={checkUpstream}
						onSetState={setUpstreamState}
						onPatch={patchUpstream}
						onDelete={deleteUpstream}
						onError={(m) => (session.pageError = m)}
					/>
				{:else if active === 'subjects'}
					<AdminSubjects
						{subjectRows}
						{subjectPageRows}
						bind:subjectForm
						bind:subjectPasswordForm
						bind:subjectSearch
						bind:subjectPasswordSearch
						bind:subjectPage
						{subjectOptions}
						onCreate={createSubject}
						onResetPassword={resetSubjectPassword}
						onPatch={patchSubject}
						onSetState={setSubjectState}
						onDelete={deleteSubject}
					/>
				{:else if active === 'projects'}
					<AdminProjects
						memberships={inventory.inventory.memberships}
						{dropdownProjects}
						{managedRoles}
						{projectRows}
						{projectPageRows}
						bind:projectForm
						bind:membershipForm
						bind:projectOwnerSearch
						bind:projectMemberSearch
						bind:projectSearch
						bind:projectPage
						{subjectOptions}
						{subjectLabel}
						{projectLabel}
						onCreateProject={createProject}
						onCreateMembership={createMembership}
						onPatchProject={patchProject}
					/>
				{:else if active === 'keys'}
					<AdminKeys
						projects={inventory.inventory.projects}
						{keyRows}
						{keyPageRows}
						bind:keyForm
						bind:keySubjectSearch
						bind:keyListSubjectSearch
						bind:keyProjectFilter
						bind:keyStateFilter
						bind:keyPage
						{dropdownProjects}
						{subjectOptions}
						{subjectLabel}
						{projectLabel}
						onIssue={issueKey}
						onSetState={setKeyState}
					/>
				{:else if active === 'teams'}
					<AdminTeams
						teams={inventory.inventory.teams}
						models={inventory.inventory.models}
						modelTeamGrants={inventory.inventory.modelTeamGrants}
						{managedRoles}
						{teamMembershipRows}
						{teamMembershipPageRows}
						bind:teamForm
						bind:teamMembershipForm
						bind:modelTeamGrantForm
						bind:teamSubjectSearch
						bind:teamMembershipTeamFilter
						bind:teamMembershipSubjectSearch
						bind:teamMembershipRoleFilter
						bind:teamMembershipStateFilter
						bind:teamMembershipPage
						{subjectOptions}
						{subjectLabel}
						{modelLabel}
						{teamLabel}
						onCreateTeam={createTeam}
						onCreateTeamMembership={createTeamMembership}
						onCreateModelTeamGrant={createModelTeamGrant}
						onPatchTeam={patchTeam}
						onSetTeamMembershipState={setTeamMembershipState}
						onSetModelTeamGrantState={setModelTeamGrantState}
					/>
				{:else if active === 'skill-market'}
					<SkillMarketSection client={session.api} teams={inventory.inventory.teams} />
				{:else if active === 'mcp-market'}
					<McpMarketSection client={session.api} teams={inventory.inventory.teams} />
				{:else if active === 'entitlements'}
					<AdminEntitlements
						models={inventory.inventory.models}
						entitlements={inventory.inventory.entitlements}
						bind:entitlementForm
						bind:entitlementSubjectSearch
						{scopeOptions}
						{subjectLabel}
						{projectLabel}
						{keyLabel}
						{modelLabel}
						onCreate={createEntitlement}
						onSetState={setEntitlementState}
					/>
				{:else if active === 'rate'}
					<AdminRate
						ratePolicies={inventory.inventory.ratePolicies}
						bind:rateForm
						bind:rateSubjectSearch
						{scopeOptions}
						{subjectLabel}
						{projectLabel}
						{keyLabel}
						onCreate={createRatePolicy}
						onSetState={setRateState}
					/>
				{:else if active === 'usage'}
					<AdminUsage
						inventory={inventory.inventory}
						realtimeStatus={inventory.realtimeStatus}
						bind:realtimeLocked={inventory.realtimeLocked}
						realtime={inventory.realtime}
						{realtimeRows}
						{realtimeUpdatedLabel}
						{totals}
						{analyticsPerformance}
						{analyticsMaxTokens}
						{visibleAnalyticsBuckets}
						{visibleAnalyticsDrilldown}
						{visibleUsageRows}
						bind:usageStart
						bind:usageEnd
						bind:analyticsBucket
						bind:analyticsDimension
						bind:modelFilter
						bind:subjectFilter
						bind:projectFilter
						bind:usageSubjectSearch
						{subjectOptions}
						{subjectLabel}
						{projectLabel}
						{setUsageRange}
						onRefreshUsageAnalytics={refreshUsageAnalytics}
						onStartRealtimeStream={inventory.startRealtimeStream}
						loading={session.loading}
					/>
				{:else if active === 'ranking'}
					<AdminRanking
						models={inventory.inventory.models}
						{rankingRows}
						{rankingPageRows}
						bind:usageStart
						bind:usageEnd
						bind:rankingModel
						bind:rankingLimit
						bind:rankingPage
						{setUsageRange}
						onRefresh={refreshUsageRanking}
						loading={session.loading}
					/>
				{:else if active === 'audit'}
					<AdminAudit
						{auditRows}
						{auditPageRows}
						bind:auditPage
						onDetail={(event) => (auditDetail = event)}
					/>
				{:else if active === 'diagnostics'}
					<AdminDiagnostics
						ready={session.ready}
						diagnostics={inventory.diagnostics}
						upstreams={inventory.inventory.upstreams}
						healthResults={inventory.healthResults}
						healthCheckConfig={inventory.healthCheckConfig}
						healthCheckToggling={inventory.healthCheckToggling}
						{modelLabel}
						onToggleHealthCheck={inventory.toggleHealthCheck}
						onCheck={checkUpstream}
						onSetState={setUpstreamState}
						onPatch={patchUpstream}
						onDelete={deleteUpstream}
						onError={(m) => (session.pageError = m)}
					/>
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

{#if session.connected && mustProvideRealName}
	<div class="modal-backdrop" role="presentation">
		<section class="modal" aria-label="补充真实姓名">
			<header>
				<h2>请补充真实姓名</h2>
				<p>为了审计用量能对应到具体人员，继续使用前必须填写真实姓名。</p>
			</header>
			<label>真实姓名<input bind:value={realNameForm.full_name} autocomplete="name" onkeydown={(event) => event.key === 'Enter' && submitRealName()} /></label>
			{#if realNameError}<p class="error">{realNameError}</p>{/if}
			<footer class="actions">
				<button type="button" onclick={submitRealName} disabled={session.loading}>{session.loading ? '保存中' : '保存并继续'}</button>
			</footer>
		</section>
	</div>
{/if}

{#if confirmOpen}
	<Modal bind:open={confirmOpen} onClose={() => resolveConfirm(false)} ariaLabel={confirmTitle} width="narrow">
		<header>
			<h2>{confirmTitle}</h2>
		</header>
		<p>{confirmMessage}</p>
		<footer class="actions">
			<button class={confirmDanger ? 'danger' : ''} type="button" onclick={() => resolveConfirm(true)}>
				{confirmConfirmLabel}
			</button>
			<button class="secondary" type="button" onclick={() => resolveConfirm(false)}>取消</button>
		</footer>
	</Modal>
{/if}

<SecretOnceDialog secret={session.plaintextKey} onClose={() => (session.plaintextKey = '')} />
