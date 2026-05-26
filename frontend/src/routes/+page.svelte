<script lang="ts">
	import { onMount } from 'svelte';
	import {
		Activity,
		BookOpen,
		Copy,
		Database,
		Gauge,
		KeyRound,
		Network,
		Route,
		Shield,
		Terminal,
		Trophy,
		UserPlus,
		Users
	} from 'lucide-svelte';
	import { AdminApiClient, isApiError } from '$lib/api/client';
	import type {
		AuditEvent,
		AuthProfile,
		Diagnostics,
		GatewayKeyCreateResponse,
		Inventory,
		IPPolicyMode,
		LoginResponse,
		OwnUsageSummary,
		ReadyStatus,
		RegisterResponse,
		ResourceState,
		RouterPolicy,
		SubjectType,
		UpstreamHealth
	} from '$lib/api/types';
	import StateBadge from '$lib/components/StateBadge.svelte';
	import JsonViewer from '$lib/components/JsonViewer.svelte';
	import CommandBlock from '$lib/components/CommandBlock.svelte';
	import SecretOnceDialog from '$lib/components/SecretOnceDialog.svelte';
	import {
		clearStoredSessionToken,
		loadStoredSessionToken,
		persistSessionToken
	} from '$lib/state/admin-token';
	import { parseCidrList, parseJsonObject, validateCidrList, validateHttpUrl, validatePort } from '$lib/validators';

	type Section = {
		id: string;
		label: string;
		group: string;
		icon: typeof Activity;
	};

	const sections: Section[] = [
		{ id: 'overview', label: '总览', group: '运行', icon: Activity },
		{ id: 'diagnostics', label: '诊断', group: '运行', icon: Database },
		{ id: 'models', label: '模型', group: '配置', icon: BookOpen },
		{ id: 'upstreams', label: '上游', group: '配置', icon: Network },
		{ id: 'router', label: 'Router 命令', group: '配置', icon: Terminal },
		{ id: 'subjects', label: '用户', group: '访问', icon: Users },
		{ id: 'projects', label: '项目', group: '访问', icon: Route },
		{ id: 'keys', label: '网关密钥', group: '访问', icon: KeyRound },
		{ id: 'teams', label: '权限组', group: '访问', icon: UserPlus },
		{ id: 'entitlements', label: '旧授权', group: '策略', icon: Shield },
		{ id: 'rate', label: '限流', group: '策略', icon: Gauge },
		{ id: 'usage', label: '用量', group: '证据', icon: Activity },
		{ id: 'ranking', label: '排行榜', group: '证据', icon: Trophy },
		{ id: 'audit', label: '审计', group: '证据', icon: Shield }
	];

	const navGroups = Array.from(new Set(sections.map((section) => section.group)));

	let active = $state('overview');
	let sessionToken = $state('');
	let rememberSession = $state(true);
	let connected = $state(false);
	let loading = $state(false);
	let pageError = $state('');
	let plaintextKey = $state('');
	let ready = $state<ReadyStatus | null>(null);
	let diagnostics = $state<Diagnostics | null>(null);
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
	let rankingLimit = $state(20);
	let rankingModel = $state('');
	let gatewayBaseUrl = $state('');
	let copiedItem = $state('');
	let auditDetail = $state<AuditEvent | null>(null);
	let generatedRouterCommand = $state('');
	let subjectSearch = $state('');
	let subjectSelectSearch = $state('');
	let cidrEditorModel = $state<Inventory['models'][number] | null>(null);
	let cidrEditorValue = $state('');
	let realNameForm = $state({ full_name: '' });
	let realNameError = $state('');

	let subjectForm = $state({ name: '', login_username: '', password: '', type: 'user' as SubjectType, notes: '' });
	let loginForm = $state({ username: '', password: '' });
	let registerForm = $state({ username: '', full_name: '', password: '' });
	let ownPasswordForm = $state({ current_password: '', new_password: '' });
	let ownKeyForm = $state({ name: '个人密钥' });
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
		litellm_model: '',
		supports_streaming: true,
		supports_tools: true,
		supports_reasoning: true,
		ip_policy_mode: 'all_pass' as IPPolicyMode,
		ip_allowlist_cidrs: '',
		notes: ''
	});
	let upstreamForm = $state({
		model_alias_id: '',
		name: '',
		base_url: '',
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
	let routerForm = $state({
		model_alias_id: '',
		worker_urls: '',
		policy: 'consistent_hash' as RouterPolicy,
		host: '0.0.0.0',
		port: 18001,
		extra_args: '{}'
	});

	const api = $derived(new AdminApiClient('', sessionToken));
	const isAdmin = $derived(Boolean(profile?.subject.is_admin));
	const mustProvideRealName = $derived(Boolean(profile?.subject.requires_real_name));
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
	const employeeIdPattern = /^[A-Za-z]\d{8}$/;

	const totals = $derived(
		usageRows.reduce(
			(acc, row) => {
				acc.requests += Number(row.request_count ?? 0);
				acc.prompt += Number(row.prompt_tokens ?? 0);
				acc.completion += Number(row.completion_tokens ?? 0);
				acc.total += Number(row.total_tokens ?? 0);
				acc.success += Number(row.success_count ?? 0);
				acc.failure += Number(row.failure_count ?? 0);
				return acc;
			},
			{ requests: 0, prompt: 0, completion: 0, total: 0, success: 0, failure: 0 }
		)
	);
	const analyticsMaxTokens = $derived(
		Math.max(1, ...inventory.analyticsBuckets.map((row) => Number(row.total_tokens ?? 0)))
	);
	const analyticsPerformance = $derived(
		inventory.analyticsBuckets.reduce(
			(acc, row) => {
				acc.requests += Number(row.request_count ?? 0);
				acc.retry += Number(row.retry_count ?? 0);
				acc.fallback += Number(row.fallback_count ?? 0);
				acc.vllmObserved += Number(row.vllm_metrics_count ?? 0);
				if (row.avg_latency_ms !== null) {
					acc.latencyTotal += row.avg_latency_ms * Number(row.request_count ?? 0);
					acc.latencyWeight += Number(row.request_count ?? 0);
				}
				if (row.avg_ttft_ms !== null) {
					acc.ttftTotal += row.avg_ttft_ms * Number(row.request_count ?? 0);
					acc.ttftWeight += Number(row.request_count ?? 0);
				}
				return acc;
			},
			{ requests: 0, retry: 0, fallback: 0, vllmObserved: 0, latencyTotal: 0, latencyWeight: 0, ttftTotal: 0, ttftWeight: 0 }
		)
	);

	onMount(() => {
		const range = defaultUsageRange();
		ownUsageStart = range.start;
		ownUsageEnd = range.end;
		gatewayBaseUrl = inferGatewayBaseUrl();
		sessionToken = loadStoredSessionToken();
		rememberSession = Boolean(sessionToken);
		void refreshReady();
		if (sessionToken) void loadProfile(true);
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
			} else {
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
			} else {
				await fetchOwnUsage();
			}
		});
	}

	function disconnect() {
		sessionToken = '';
		profile = null;
		connected = false;
		plaintextKey = '';
		copiedItem = '';
		pageError = '';
		clearStoredSessionToken();
		inventory = emptyInventory();
	}

	async function refreshReady() {
		try {
			const response = await fetch('/health/ready');
			ready = (await response.json()) as ReadyStatus;
		} catch {
			ready = null;
		}
	}

	async function refreshAll() {
		await run(async () => {
			await refreshReady();
			if (!isAdmin) {
				profile = await api.get<AuthProfile>('/auth/me');
				await fetchOwnUsage();
				return;
			}
			const [
				subjects,
				projects,
				memberships,
				keys,
				models,
				entitlements,
				teams,
				teamMemberships,
				modelTeamGrants,
				upstreams,
				ratePolicies,
				usage,
				ranking,
				analyticsBuckets,
				analyticsDrilldown,
				audit
			] = await Promise.all([
				api.get<Inventory['subjects']>('/admin/subjects'),
				api.get<Inventory['projects']>('/admin/projects'),
				api.get<Inventory['memberships']>('/admin/project-memberships'),
				api.get<Inventory['keys']>('/admin/gateway-keys'),
				api.get<Inventory['models']>('/admin/model-aliases'),
				api.get<Inventory['entitlements']>('/admin/model-entitlements'),
				api.get<Inventory['teams']>('/admin/teams'),
				api.get<Inventory['teamMemberships']>('/admin/team-memberships'),
				api.get<Inventory['modelTeamGrants']>('/admin/model-team-grants'),
				api.get<Inventory['upstreams']>('/admin/upstreams'),
				api.get<Inventory['ratePolicies']>('/admin/rate-policies'),
				api.get<Inventory['usage']>('/admin/usage/summary', { start: usageStart, end: usageEnd }),
				api.get<Inventory['ranking']>('/admin/usage/ranking', { start: usageStart, end: usageEnd, model: rankingModel, limit: rankingLimit }),
				api.get<Inventory['analyticsBuckets']>('/admin/analytics/time-buckets', {
					start: usageStart,
					end: usageEnd,
					bucket: analyticsBucket,
					model: modelFilter,
					subject_id: subjectFilter,
					project_id: projectFilter
				}),
				api.get<Inventory['analyticsDrilldown']>('/admin/analytics/drilldown', {
					start: usageStart,
					end: usageEnd,
					dimension: analyticsDimension,
					model: modelFilter,
					subject_id: subjectFilter,
					project_id: projectFilter
				}),
				api.get<Inventory['audit']>('/admin/audit-events')
			]);
			inventory = {
				subjects,
				projects,
				memberships,
				keys,
				models,
				entitlements,
				teams,
				teamMemberships,
				modelTeamGrants,
				upstreams,
				routerConfigs: [],
				ratePolicies,
				usage,
				ranking,
				analyticsBuckets,
				analyticsDrilldown,
				audit
			};
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
					...modelForm,
					ip_allowlist_cidrs:
						modelForm.ip_policy_mode === 'allowlist' ? parseCidrList(modelForm.ip_allowlist_cidrs) : []
				})
			);
			modelForm = {
				alias: '',
				upstream_model_name: '',
				litellm_model: '',
				supports_streaming: true,
				supports_tools: true,
				supports_reasoning: true,
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

	async function createRouterConfig() {
		pageError = '';
		generatedRouterCommand = '';
		const portCheck = validatePort(Number(routerForm.port));
		if (!portCheck.ok) {
			pageError = portCheck.message ?? '端口不合法';
			return;
		}
		const worker_urls = routerForm.worker_urls
			.split(/\n/)
			.map((item) => item.trim())
			.filter(Boolean);
		if (!worker_urls.length || worker_urls.some((url) => !validateHttpUrl(url, 'Worker URL').ok)) {
			pageError = '每个 Worker URL 都必须以 http:// 或 https:// 开头。';
			return;
		}
		let extraArgs: Record<string, unknown>;
		try {
			extraArgs = parseJsonObject(routerForm.extra_args, '额外参数');
		} catch (error) {
			pageError = errorMessage(error);
			return;
		}
		generatedRouterCommand = renderRouterCommand({
			worker_urls,
			policy: routerForm.policy,
			host: routerForm.host,
			port: Number(routerForm.port),
			extra_args: extraArgs
		});
	}

	function renderRouterCommand(config: {
		worker_urls: string[];
		policy: RouterPolicy;
		host: string;
		port: number;
		extra_args: Record<string, unknown>;
	}): string {
		const args = [
			'vllm-router',
			'--worker-urls',
			...config.worker_urls,
			'--policy',
			config.policy,
			'--host',
			config.host,
			'--port',
			String(config.port)
		];
		for (const key of Object.keys(config.extra_args).sort()) {
			const value = config.extra_args[key];
			const flag = `--${key.replaceAll('_', '-')}`;
			if (typeof value === 'boolean') {
				if (value) args.push(flag);
				continue;
			}
			if (Array.isArray(value)) {
				args.push(flag, ...value.map(String));
				continue;
			}
			if (value !== null && value !== undefined) args.push(flag, String(value));
		}
		return args.map(shellQuote).join(' ');
	}

	function shellQuote(value: string): string {
		return value && /^[A-Za-z0-9_\-./:=,]+$/.test(value) ? value : `'${value.replaceAll("'", "'\"'\"'")}'`;
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

	function defaultUsageRange() {
		const end = new Date();
		const start = new Date(end.getTime() - 7 * 24 * 60 * 60 * 1000);
		return { start: toDateTimeLocal(start), end: toDateTimeLocal(end) };
	}

	function toDateTimeLocal(date: Date): string {
		const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
		return local.toISOString().slice(0, 16);
	}

	function inferGatewayBaseUrl(): string {
		if (typeof window === 'undefined') return '';
		return window.location.origin;
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
			routerConfigs: [],
			ratePolicies: [],
			usage: [],
			ranking: [],
			analyticsBuckets: [],
			analyticsDrilldown: [],
			audit: []
		};
	}

	function clean<T extends Record<string, unknown>>(value: T): T {
		const result: Record<string, unknown> = {};
		for (const [key, item] of Object.entries(value)) {
			result[key] = item === '' ? null : item;
		}
		return result as T;
	}

	function errorMessage(error: unknown): string {
		if (isApiError(error)) return `${error.status}: ${error.message}`;
		if (error instanceof Error) return error.message;
		return '发生未知错误';
	}

	function short(id: string | null | undefined): string {
		if (!id) return '无';
		return id.length > 12 ? `${id.slice(0, 8)}…` : id;
	}

	function subjectLabel(id: string | null | undefined): string {
		const subject = inventory.subjects.find((item) => item.id === id);
		return subject ? subjectDisplay(subject) : short(id);
	}

	function projectLabel(id: string | null | undefined): string {
		return inventory.projects.find((item) => item.id === id)?.name ?? short(id);
	}

	function keyLabel(id: string | null | undefined): string {
		const key = inventory.keys.find((item) => item.id === id);
		return key ? `${key.name} (${key.key_prefix})` : short(id);
	}

	function modelLabel(id: string | null | undefined): string {
		return inventory.models.find((item) => item.id === id)?.alias ?? short(id);
	}

	function teamLabel(id: string | null | undefined): string {
		return inventory.teams.find((item) => item.id === id)?.name ?? short(id);
	}

	function msLabel(value: number | null | undefined): string {
		return value === null || value === undefined ? '无数据' : `${Math.round(value)} ms`;
	}

	function ratioLabel(value: number | null | undefined): string {
		return value === null || value === undefined ? '无数据' : `${Math.round(value * 100)}%`;
	}

	function subjectTypeLabel(type: string): string {
		return type === 'service' ? '服务账号' : '用户';
	}

	function scopeLabel(scope: string): string {
		if (scope === 'subject') return '用户';
		if (scope === 'project') return '项目';
		if (scope === 'key') return '密钥';
		return scope;
	}

	function scopeOptions(scope: string) {
		if (scope === 'subject') return filteredSubjects(subjectSelectSearch).map((item) => ({ id: item.id, label: subjectDisplay(item) }));
		if (scope === 'project') return inventory.projects.map((item) => ({ id: item.id, label: item.name }));
		return inventory.keys.map((item) => ({ id: item.id, label: `${item.name} (${item.key_prefix})` }));
	}

	function subjectDisplay(subject: Inventory['subjects'][number]): string {
		return subject.login_username ? `${subject.name} / ${subject.login_username}` : subject.name;
	}

	function filteredSubjects(query: string) {
		const needle = query.trim().toLowerCase();
		if (!needle) return inventory.subjects;
		return inventory.subjects.filter((subject) =>
			[subject.name, subject.login_username ?? '', subject.notes ?? ''].some((value) =>
				value.toLowerCase().includes(needle)
			)
		);
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
	<div class="app">
		<aside class="sidebar">
			<div class="brand">
				<strong>LLM Gateway</strong>
				<span>账号访问</span>
			</div>
		</aside>
		<main class="main">
			<section class="content">
				<div class="split" style="align-items: start;">
				<div class="panel">
					<h1>登录</h1>
					<p>使用你的网关账号进入控制台。</p>
					{#if ready}
						<div class="actions">
							<StateBadge value={ready.ok ? 'ready' : 'not_ready'} tone={ready.ok ? 'success' : 'danger'} />
							<span class="muted">Postgres {ready.checks.postgres ? '正常' : '异常'} · Redis {ready.checks.redis ? '正常' : '异常'}</span>
						</div>
					{/if}
					<label>
						用户名
						<input bind:value={loginForm.username} autocomplete="username" />
					</label>
					<label>
						密码
						<input type="password" bind:value={loginForm.password} autocomplete="current-password" onkeydown={(event) => event.key === 'Enter' && loginAccount()} />
					</label>
					<label style="display: flex; grid-template-columns: auto 1fr; align-items: center;">
						<input type="checkbox" bind:checked={rememberSession} style="width: auto;" />
						在这台设备上记住登录
					</label>
					{#if pageError}<div class="error">{pageError}</div>{/if}
					<div class="actions">
						<button type="button" onclick={() => loginAccount()} disabled={loading}>{loading ? '登录中' : '登录'}</button>
						<button class="secondary" type="button" onclick={refreshReady}>刷新就绪状态</button>
					</div>
				</div>
				<div class="panel">
					<h1>注册</h1>
					<p>新用户会自动加入 <code>guest</code> 权限组，并立即获得一个网关密钥。</p>
					<label>工号<input bind:value={registerForm.username} autocomplete="username" placeholder="l00014624" /></label>
					<label>真实姓名<input bind:value={registerForm.full_name} autocomplete="name" /></label>
					<label>密码<input type="password" bind:value={registerForm.password} autocomplete="new-password" /></label>
					<div class="actions">
						<button type="button" onclick={registerAccount} disabled={loading}>创建账号</button>
					</div>
				</div>
				</div>
			</section>
		</main>
	</div>
{:else}
	<div class="app">
		<aside class="sidebar">
			<div class="brand">
				<strong>LLM Gateway</strong>
				<span>{diagnostics?.environment ?? '环境未知'} · LiteLLM {diagnostics?.litellm_version ?? '未知'}</span>
			</div>
			{#if !isAdmin}
				<nav class="nav-group" aria-label="账号">
					<div class="nav-group-title">账号</div>
					<button class="active nav-button" type="button"><span>我的访问权限</span><KeyRound size={16} /></button>
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
					{#if isAdmin}
						<label style="min-width: 220px;">搜索用户<input bind:value={subjectSelectSearch} placeholder="姓名或工号" /></label>
					{/if}
					<button class="secondary" type="button" onclick={refreshAll} disabled={loading}>{loading ? '处理中' : '刷新'}</button>
					<button class="secondary" type="button" onclick={disconnect}>退出登录</button>
				</div>
			</div>
			<section class="content">
				{#if pageError}<div class="error">{pageError}</div>{/if}

				{#if !isAdmin}
					<div class="page-header"><div><h1>我的访问权限</h1><p>{profile?.subject.login_username ?? profile?.subject.name}</p></div></div>
					<div class="grid">
						<div class="metric"><span>权限组</span><strong>{profile?.teams.join(', ') || '无'}</strong></div>
						<div class="metric"><span>可用模型</span><strong>{profile?.models.length ?? 0}</strong></div>
						<div class="metric"><span>密钥</span><strong>{profile?.keys.length ?? 0}</strong></div>
						<div class="metric"><span>当前范围请求数</span><strong>{ownUsage?.request_count ?? 0}</strong></div>
						<div class="metric"><span>当前范围总 token</span><strong>{ownUsage?.total_tokens ?? 0}</strong></div>
					</div>
					<section class="panel">
						<h2>我的用量</h2>
						<div class="form-grid">
							<label>开始时间<input type="datetime-local" bind:value={ownUsageStart} /></label>
							<label>结束时间<input type="datetime-local" bind:value={ownUsageEnd} /></label>
							<button type="button" onclick={refreshOwnUsage} disabled={loading}>{loading ? '查询中' : '查询用量'}</button>
						</div>
						<div class="grid">
							<div class="metric"><span>请求数</span><strong>{ownUsage?.request_count ?? 0}</strong></div>
							<div class="metric"><span>输入 token</span><strong>{ownUsage?.prompt_tokens ?? 0}</strong></div>
							<div class="metric"><span>输出 token</span><strong>{ownUsage?.completion_tokens ?? 0}</strong></div>
							<div class="metric"><span>总 token</span><strong>{ownUsage?.total_tokens ?? 0}</strong></div>
							<div class="metric"><span>成功 / 失败</span><strong>{ownUsage?.success_count ?? 0} / {ownUsage?.failure_count ?? 0}</strong></div>
						</div>
					</section>
					<section class="panel">
						<h2>可用模型</h2>
						<div class="table-wrap"><table><thead><tr><th>模型别名</th></tr></thead><tbody>{#each profile?.models ?? [] as model}<tr><td>{model}</td></tr>{:else}<tr><td>还没有可用模型。</td></tr>{/each}</tbody></table></div>
					</section>
					<section class="panel">
						<h2>网关密钥</h2>
						<div class="form-grid"><label>新密钥名称<input bind:value={ownKeyForm.name} /></label><button type="button" onclick={issueOwnKey}>签发密钥</button></div>
						<div class="table-wrap"><table><thead><tr><th>名称</th><th>前缀</th><th>状态</th></tr></thead><tbody>{#each profile?.keys ?? [] as key}<tr><td>{key.name}</td><td><code>{key.key_prefix}</code></td><td><StateBadge value={key.state} /></td></tr>{:else}<tr><td colspan="3">还没有密钥。</td></tr>{/each}</tbody></table></div>
					</section>
					<section class="panel">
						<h2>工具接入</h2>
						<div class="form-grid">
							<label>网关入口<input bind:value={gatewayBaseUrl} /></label>
							<label>首选模型<input value={preferredModel} readonly /></label>
							<label>当前密钥前缀<input value={visibleKeyHint} readonly /></label>
						</div>
						<div class="endpoint-grid">
							{@render CopyValue('OpenAI Base URL', gatewayV1Base, 'openai-base')}
							{@render CopyValue('Responses Endpoint', responsesEndpoint, 'responses-endpoint')}
							{@render CopyValue('Claude Messages Endpoint', messagesEndpoint, 'messages-endpoint')}
							{@render CopyValue('Claude Base URL', gatewayOrigin, 'claude-base')}
						</div>
						<div class="doc-grid">
							<section class="doc-panel">
								<h3>Codex</h3>
								<p>Codex 走 OpenAI Responses 协议，Base URL 使用前端入口的 <code>/v1</code>，实际请求会落到 <code>/v1/responses</code>。</p>
								<CommandBlock command={codexEnvCommand} />
								<CommandBlock command={codexConfigCommand} />
							</section>
							<section class="doc-panel">
								<h3>Claude Code</h3>
								<p>Claude Code 走 Anthropic Messages 协议，Base URL 填前端入口，不带 <code>/v1/messages</code>；客户端会自己拼接 <code>/v1/messages</code>。如果模型别名不是 <code>claude</code> 或 <code>anthropic</code> 开头，用自定义模型变量把它放进选择器。</p>
								<CommandBlock command={claudeEnvCommand} />
							</section>
						</div>
					</section>
					<section class="panel">
						<h2>修改密码</h2>
						<div class="form-grid">
							<label>当前密码<input type="password" bind:value={ownPasswordForm.current_password} autocomplete="current-password" /></label>
							<label>新密码<input type="password" bind:value={ownPasswordForm.new_password} autocomplete="new-password" /></label>
							<button type="button" onclick={changeOwnPassword}>修改密码</button>
						</div>
					</section>
				{:else if active === 'overview'}
					<div class="page-header">
						<div>
							<h1>总览</h1>
							<p>运行状态、近期推理压力和最新管理变更。</p>
						</div>
					</div>
					<div class="grid">
						<div class="metric"><span>请求数</span><strong>{totals.requests}</strong></div>
						<div class="metric"><span>总 token</span><strong>{totals.total}</strong></div>
						<div class="metric"><span>输入 token</span><strong>{totals.prompt}</strong></div>
						<div class="metric"><span>输出 token</span><strong>{totals.completion}</strong></div>
						<div class="metric"><span>成功 / 失败</span><strong>{totals.success} / {totals.failure}</strong></div>
					</div>
					<div class="split">
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
							<h2>最近审计</h2>
							{@render AuditTable(inventory.audit.slice(0, 6), (event) => (auditDetail = event))}
						</section>
					</div>
				{:else if active === 'models'}
					{@render PageTitle('模型别名', '配置下游模型名称、LiteLLM 映射、能力标记和模型级 IP 策略。')}
					<section class="panel">
						<h2>创建模型别名</h2>
						<div class="form-grid">
							<label>别名<input bind:value={modelForm.alias} placeholder="dev-model" /></label>
							<label>上游模型名<input bind:value={modelForm.upstream_model_name} /></label>
							<label>LiteLLM 模型<input bind:value={modelForm.litellm_model} placeholder="openai/model-name" /></label>
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
								<thead><tr><th>别名</th><th>LiteLLM</th><th>状态</th><th>IP 策略</th><th>Streaming</th><th>Tools</th><th>Reasoning</th><th>操作</th></tr></thead>
								<tbody>
									{#each inventory.models as model}
										<tr>
											<td><strong>{model.alias}</strong><br /><span class="muted">{model.upstream_model_name}</span></td>
											<td>{model.litellm_model}</td>
											<td><StateBadge value={model.state} /></td>
											<td><StateBadge value={model.ip_policy_mode} /><br /><span class="muted">{model.ip_allowlist_cidrs.join(', ') || '未配置 CIDR'}</span></td>
											<td><StateBadge value={model.supports_streaming} tone="accent" /></td>
											<td><StateBadge value={model.supports_tools} tone="accent" /></td>
											<td><StateBadge value={model.supports_reasoning} tone="accent" /></td>
											<td class="actions"><button class="secondary" type="button" onclick={() => editModelCidrs(model)}>编辑 CIDR</button><button class="secondary" type="button" onclick={() => patchModel(model.id, { state: model.state === 'active' ? 'disabled' : 'active' })}>{model.state === 'active' ? '禁用' : '启用'}</button><button class="danger" type="button" onclick={() => deleteModel(model)}>删除</button></td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					</section>
				{:else if active === 'upstreams'}
					{@render PageTitle('上游端点', '模型别名背后的 OpenAI 兼容上游或 vLLM Router 聚合池。')}
					<section class="panel">
						<h2>创建上游</h2>
						<div class="form-grid">
							<label>模型<select bind:value={upstreamForm.model_alias_id}><option value="">选择模型</option>{#each inventory.models as model}<option value={model.id}>{model.alias}</option>{/each}</select></label>
							<label>名称<input bind:value={upstreamForm.name} /></label>
							<label>Base URL<input bind:value={upstreamForm.base_url} placeholder="http://host:8000/v1" /></label>
							<label>健康检查路径<input bind:value={upstreamForm.health_path} /></label>
							<label>API key 引用<input bind:value={upstreamForm.api_key_ref} /></label>
							<label>API key 明文<input type="password" bind:value={upstreamForm.api_key_value} /></label>
							<label>额外请求头<textarea bind:value={upstreamForm.extra_headers}></textarea></label>
							<button type="button" onclick={createUpstream}>创建上游</button>
						</div>
					</section>
						{@render UpstreamTable(inventory.upstreams, healthResults, modelLabel, checkUpstream, setUpstreamState, deleteUpstream)}
				{:else if active === 'subjects'}
					{@render PageTitle('用户', '由网关管理的人类用户和服务账号。')}
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
								<label>搜索用户<input bind:value={subjectSelectSearch} placeholder="输入姓名或工号" /></label>
								<label>用户<select bind:value={subjectPasswordForm.subject_id}><option value="">选择用户</option>{#each filteredSubjects(subjectSelectSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label>
								<label>新密码<input type="password" bind:value={subjectPasswordForm.new_password} /></label>
								<button type="button" onclick={resetSubjectPassword}>重置密码</button>
							</div>
						</section>
						<section class="panel">
							<h2>用户</h2>
							<label>搜索用户<input bind:value={subjectSearch} placeholder="输入姓名、工号或备注" /></label>
							<div class="table-wrap"><table><thead><tr><th>真实姓名</th><th>工号</th><th>类型</th><th>状态</th><th>备注</th><th>操作</th></tr></thead><tbody>{#each filteredSubjects(subjectSearch) as subject}<tr><td>{subject.name}<br /><span class="muted">{short(subject.id)}</span></td><td>{subject.login_username ?? '无'}</td><td>{subjectTypeLabel(subject.type)}</td><td><StateBadge value={subject.state} /></td><td>{subject.notes}</td><td class="actions"><button class="secondary" type="button" onclick={() => patchSubject(subject.id, { name: prompt('真实姓名', subject.name) ?? subject.name })}>编辑姓名</button><button class="secondary" type="button" onclick={() => patchSubject(subject.id, { notes: prompt('备注', subject.notes ?? '') ?? subject.notes })}>编辑备注</button><button class="secondary" type="button" onclick={() => setSubjectState(subject.id, subject.state === 'active' ? 'disabled' : 'active')}>{subject.state === 'active' ? '禁用' : '启用'}</button><button class="danger" type="button" onclick={() => deleteSubject(subject)}>删除</button></td></tr>{:else}<tr><td colspan="6" class="empty">没有匹配的用户。</td></tr>{/each}</tbody></table></div>
						</section>
				{:else if active === 'projects'}
					{@render PageTitle('项目', '用量归因和项目成员关系。')}
					<div class="split">
						<section class="panel">
							<h2>创建项目</h2>
							<div class="form-grid">
								<label>名称<input bind:value={projectForm.name} /></label>
								<label>负责人<select bind:value={projectForm.owner_subject_id}><option value="">无</option>{#each filteredSubjects(subjectSelectSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label>
								<label>备注<input bind:value={projectForm.notes} /></label>
								<button type="button" onclick={createProject}>创建项目</button>
							</div>
						</section>
						<section class="panel">
							<h2>添加项目成员</h2>
							<div class="form-grid">
								<label>项目<select bind:value={membershipForm.project_id}><option value="">项目</option>{#each inventory.projects as project}<option value={project.id}>{project.name}</option>{/each}</select></label>
								<label>用户<select bind:value={membershipForm.subject_id}><option value="">用户</option>{#each filteredSubjects(subjectSelectSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label>
								<label>角色<input bind:value={membershipForm.role} /></label>
								<button type="button" onclick={createMembership}>添加成员</button>
							</div>
						</section>
					</div>
					<section class="panel"><h2>项目</h2><div class="table-wrap"><table><thead><tr><th>名称</th><th>负责人</th><th>状态</th><th>备注</th><th>操作</th></tr></thead><tbody>{#each inventory.projects as project}<tr><td>{project.name}<br /><span class="muted">{short(project.id)}</span></td><td>{subjectLabel(project.owner_subject_id)}</td><td><StateBadge value={project.state} /></td><td>{project.notes}</td><td><button class="secondary" type="button" onclick={() => patchProject(project.id, { notes: prompt('备注', project.notes ?? '') ?? project.notes })}>编辑备注</button></td></tr>{/each}</tbody></table></div></section>
					<section class="panel"><h2>项目成员</h2><div class="table-wrap"><table><thead><tr><th>项目</th><th>用户</th><th>角色</th></tr></thead><tbody>{#each inventory.memberships as membership}<tr><td>{projectLabel(membership.project_id)}</td><td>{subjectLabel(membership.subject_id)}</td><td>{membership.role}</td></tr>{/each}</tbody></table></div></section>
				{:else if active === 'keys'}
					{@render PageTitle('网关密钥', '签发、轮换和停用网关管理的密钥。')}
					<section class="panel"><h2>签发密钥</h2><div class="form-grid"><label>用户<select bind:value={keyForm.subject_id}><option value="">用户</option>{#each filteredSubjects(subjectSelectSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label><label>项目<select bind:value={keyForm.project_id}><option value="">项目</option>{#each inventory.projects as project}<option value={project.id}>{project.name}</option>{/each}</select></label><label>名称<input bind:value={keyForm.name} /></label><button type="button" onclick={issueKey}>签发密钥</button></div></section>
					<section class="panel"><h2>密钥</h2><div class="table-wrap"><table><thead><tr><th>名称</th><th>前缀</th><th>用户</th><th>项目</th><th>状态</th><th>操作</th></tr></thead><tbody>{#each inventory.keys as key}<tr><td>{key.name}</td><td><code>{key.key_prefix}</code></td><td>{subjectLabel(key.subject_id)}</td><td>{projectLabel(key.project_id)}</td><td><StateBadge value={key.state} /></td><td><button class="secondary" type="button" onclick={() => setKeyState(key.id, key.state === 'active' ? 'disabled' : 'active')}>{key.state === 'active' ? '禁用' : '启用'}</button></td></tr>{/each}</tbody></table></div></section>
				{:else if active === 'teams'}
					{@render PageTitle('权限组', '自助注册用户会继承其所有启用权限组的模型访问权限。')}
					<div class="split">
						<section class="panel"><h2>创建权限组</h2><div class="form-grid"><label>名称<input bind:value={teamForm.name} /></label><label>备注<input bind:value={teamForm.notes} /></label><button type="button" onclick={createTeam}>创建权限组</button></div></section>
						<section class="panel"><h2>把用户加入权限组</h2><div class="form-grid"><label>权限组<select bind:value={teamMembershipForm.team_id}><option value="">权限组</option>{#each inventory.teams as team}<option value={team.id}>{team.name}</option>{/each}</select></label><label>用户<select bind:value={teamMembershipForm.subject_id}><option value="">用户</option>{#each filteredSubjects(subjectSelectSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label><label>角色<input bind:value={teamMembershipForm.role} /></label><button type="button" onclick={createTeamMembership}>添加成员</button></div></section>
					</div>
					<section class="panel"><h2>给权限组授权模型</h2><div class="form-grid"><label>模型<select bind:value={modelTeamGrantForm.model_alias_id}><option value="">模型</option>{#each inventory.models as model}<option value={model.id}>{model.alias}</option>{/each}</select></label><label>权限组<select bind:value={modelTeamGrantForm.team_id}><option value="">权限组</option>{#each inventory.teams as team}<option value={team.id}>{team.name}</option>{/each}</select></label><button type="button" onclick={createModelTeamGrant}>授权模型</button></div></section>
					<section class="panel"><h2>权限组</h2><div class="table-wrap"><table><thead><tr><th>名称</th><th>状态</th><th>内置</th><th>备注</th><th>操作</th></tr></thead><tbody>{#each inventory.teams as team}<tr><td>{team.name}<br /><span class="muted">{short(team.id)}</span></td><td><StateBadge value={team.state} /></td><td><StateBadge value={team.is_builtin} tone="accent" /></td><td>{team.notes}</td><td><button class="secondary" type="button" onclick={() => patchTeam(team.id, { state: team.state === 'active' ? 'disabled' : 'active' })}>{team.state === 'active' ? '禁用' : '启用'}</button></td></tr>{/each}</tbody></table></div></section>
					<section class="panel"><h2>成员关系</h2><div class="table-wrap"><table><thead><tr><th>权限组</th><th>用户</th><th>角色</th><th>状态</th><th>操作</th></tr></thead><tbody>{#each inventory.teamMemberships as membership}<tr><td>{teamLabel(membership.team_id)}</td><td>{subjectLabel(membership.subject_id)}</td><td>{membership.role}</td><td><StateBadge value={membership.state} /></td><td><button class="secondary" type="button" onclick={() => setTeamMembershipState(membership.id, membership.state === 'active' ? 'disabled' : 'active')}>{membership.state === 'active' ? '禁用' : '启用'}</button></td></tr>{/each}</tbody></table></div></section>
					<section class="panel"><h2>模型授权</h2><div class="table-wrap"><table><thead><tr><th>模型</th><th>权限组</th><th>状态</th><th>操作</th></tr></thead><tbody>{#each inventory.modelTeamGrants as grant}<tr><td>{modelLabel(grant.model_alias_id)}</td><td>{teamLabel(grant.team_id)}</td><td><StateBadge value={grant.state} /></td><td><button class="secondary" type="button" onclick={() => setModelTeamGrantState(grant.id, grant.state === 'active' ? 'disabled' : 'active')}>{grant.state === 'active' ? '禁用' : '启用'}</button></td></tr>{/each}</tbody></table></div></section>
				{:else if active === 'entitlements'}
					{@render PageTitle('旧授权', '给项目、用户或单个网关密钥授予模型访问权限。')}
					<section class="panel"><h2>创建授权</h2><div class="form-grid"><label>模型<select bind:value={entitlementForm.model_alias_id}><option value="">模型</option>{#each inventory.models as model}<option value={model.id}>{model.alias}</option>{/each}</select></label><label>范围<select bind:value={entitlementForm.scope} onchange={() => (entitlementForm.scope_id = '')}><option value="project">项目</option><option value="subject">用户</option><option value="key">密钥</option></select></label><label>授权对象<select bind:value={entitlementForm.scope_id}><option value="">对象</option>{#each scopeOptions(entitlementForm.scope) as option}<option value={option.id}>{option.label}</option>{/each}</select></label><button type="button" onclick={createEntitlement}>授权访问</button></div></section>
					<section class="panel"><h2>授权</h2><div class="table-wrap"><table><thead><tr><th>模型</th><th>范围</th><th>状态</th><th>操作</th></tr></thead><tbody>{#each inventory.entitlements as entitlement}<tr><td>{modelLabel(entitlement.model_alias_id)}</td><td>{entitlement.project_id ? `项目: ${projectLabel(entitlement.project_id)}` : entitlement.subject_id ? `用户: ${subjectLabel(entitlement.subject_id)}` : `密钥: ${keyLabel(entitlement.gateway_key_id)}`}</td><td><StateBadge value={entitlement.state} /></td><td><button class="secondary" type="button" onclick={() => setEntitlementState(entitlement.id, entitlement.state === 'active' ? 'disabled' : 'active')}>{entitlement.state === 'active' ? '禁用' : '启用'}</button></td></tr>{/each}</tbody></table></div></section>
				{:else if active === 'rate'}
					{@render PageTitle('限流策略', '基于数据库配置的每分钟请求数和并发限制。')}
					<section class="panel"><h2>创建限流策略</h2><p>实际生效限制会取密钥、用户、项目和环境默认值中的最小启用策略。</p><div class="form-grid"><label>范围<select bind:value={rateForm.scope} onchange={() => (rateForm.scope_id = '')}><option value="key">密钥</option><option value="subject">用户</option><option value="project">项目</option></select></label><label>对象<select bind:value={rateForm.scope_id}><option value="">对象</option>{#each scopeOptions(rateForm.scope) as option}<option value={option.id}>{option.label}</option>{/each}</select></label><label>每分钟请求数<input type="number" min="0" bind:value={rateForm.requests_per_minute} /></label><label>并发限制<input type="number" min="0" bind:value={rateForm.concurrency_limit} /></label><button type="button" onclick={createRatePolicy}>创建策略</button></div></section>
					<section class="panel"><h2>策略</h2><div class="table-wrap"><table><thead><tr><th>范围</th><th>对象</th><th>RPM</th><th>并发</th><th>状态</th><th>操作</th></tr></thead><tbody>{#each inventory.ratePolicies as policy}<tr><td>{scopeLabel(policy.scope)}</td><td>{policy.scope === 'subject' ? subjectLabel(policy.scope_id) : policy.scope === 'project' ? projectLabel(policy.scope_id) : keyLabel(policy.scope_id)}</td><td>{policy.requests_per_minute ?? '继承'}</td><td>{policy.concurrency_limit ?? '继承'}</td><td><StateBadge value={policy.state} /></td><td><button class="secondary" type="button" onclick={() => setRateState(policy.id, policy.state === 'active' ? 'disabled' : 'active')}>{policy.state === 'active' ? '禁用' : '启用'}</button></td></tr>{/each}</tbody></table></div></section>
				{:else if active === 'router'}
					{@render PageTitle('Router 命令', '一次性生成 vLLM Router 启动命令；当前 MVP 不负责启动或托管 Router 进程。')}
					<section class="panel"><h2>生成命令</h2><div class="form-grid"><label>模型<select bind:value={routerForm.model_alias_id}><option value="">模型</option>{#each inventory.models as model}<option value={model.id}>{model.alias}</option>{/each}</select></label><label>路由策略<select bind:value={routerForm.policy}><option value="consistent_hash">consistent_hash</option><option value="cache_aware">cache_aware</option></select></label><label>监听地址<input bind:value={routerForm.host} /></label><label>端口<input type="number" bind:value={routerForm.port} /></label><label>Worker URLs<textarea bind:value={routerForm.worker_urls} placeholder="http://gpu-a:8000&#10;http://gpu-b:8000"></textarea></label><label>额外参数<textarea bind:value={routerForm.extra_args}></textarea></label><button type="button" onclick={createRouterConfig}>生成命令</button></div></section>
					{#if generatedRouterCommand}
						<section class="panel"><h2>本次生成结果</h2><CommandBlock command={generatedRouterCommand} /></section>
					{/if}
				{:else if active === 'usage'}
					{@render PageTitle('用量', '分时段、分模型、分项目和分用户观察推理压力。')}
					<section class="panel"><div class="form-grid"><label>开始时间<input type="datetime-local" bind:value={usageStart} /></label><label>结束时间<input type="datetime-local" bind:value={usageEnd} /></label><label>时间粒度<select bind:value={analyticsBucket}><option value="minute">分钟</option><option value="hour">小时</option><option value="day">天</option></select></label><label>分析维度<select bind:value={analyticsDimension}><option value="model">模型</option><option value="subject">用户</option><option value="project">项目</option><option value="endpoint">协议</option><option value="outcome">结果</option><option value="streaming">流式</option></select></label><label>模型筛选<select bind:value={modelFilter}><option value="">全部</option>{#each inventory.models as model}<option value={model.alias}>{model.alias}</option>{/each}</select></label><label>用户筛选<select bind:value={subjectFilter}><option value="">全部</option>{#each filteredSubjects(subjectSelectSearch) as subject}<option value={subject.id}>{subjectDisplay(subject)}</option>{/each}</select></label><label>项目筛选<select bind:value={projectFilter}><option value="">全部</option>{#each inventory.projects as project}<option value={project.id}>{project.name}</option>{/each}</select></label><button type="button" onclick={refreshAll}>查询</button></div></section>
					<div class="grid"><div class="metric"><span>请求数</span><strong>{totals.requests}</strong></div><div class="metric"><span>总 token</span><strong>{totals.total}</strong></div><div class="metric"><span>成功</span><strong>{totals.success}</strong></div><div class="metric"><span>失败</span><strong>{totals.failure}</strong></div><div class="metric"><span>平均延迟</span><strong>{msLabel(analyticsPerformance.latencyWeight ? analyticsPerformance.latencyTotal / analyticsPerformance.latencyWeight : null)}</strong></div><div class="metric"><span>平均 TTFT</span><strong>{msLabel(analyticsPerformance.ttftWeight ? analyticsPerformance.ttftTotal / analyticsPerformance.ttftWeight : null)}</strong></div><div class="metric"><span>Retry / Fallback</span><strong>{analyticsPerformance.retry} / {analyticsPerformance.fallback}</strong></div><div class="metric"><span>vLLM 指标覆盖</span><strong>{analyticsPerformance.vllmObserved} / {analyticsPerformance.requests}</strong></div></div>
					<section class="panel"><h2>分时段趋势</h2>{@render AnalyticsBucketTable(inventory.analyticsBuckets, analyticsMaxTokens)}</section>
					<section class="panel"><h2>多维 Drilldown</h2>{@render AnalyticsDrilldownTable(inventory.analyticsDrilldown)}</section>
					<section class="panel"><h2>汇总明细</h2>{@render UsageTable(usageRows, subjectLabel, projectLabel)}</section>
				{:else if active === 'ranking'}
					{@render PageTitle('排行榜', '按时间范围统计 token 用量最高的用户。')}
					<section class="panel"><div class="form-grid"><label>开始时间<input type="datetime-local" bind:value={usageStart} /></label><label>结束时间<input type="datetime-local" bind:value={usageEnd} /></label><label>模型筛选<select bind:value={rankingModel}><option value="">全部</option>{#each inventory.models as model}<option value={model.alias}>{model.alias}</option>{/each}</select></label><label>Top N<input type="number" bind:value={rankingLimit} min="1" max="100" /></label><button type="button" onclick={refreshAll}>查询</button></div></section>
					<section class="panel"><div class="table-wrap"><table><thead><tr><th>#</th><th>用户 / Subject</th><th>请求数</th><th>输入 token</th><th>输出 token</th><th>总 token</th></tr></thead><tbody>{#each inventory.ranking as row, i}<tr><td>{i + 1}</td><td>{row.subject_name} / {row.login_username ?? row.subject_id}</td><td>{row.request_count}</td><td>{row.prompt_tokens}</td><td>{row.completion_tokens}</td><td>{row.total_tokens}</td></tr>{:else}<tr><td colspan="6" class="empty">暂无用量数据。</td></tr>{/each}</tbody></table></div></section>
				{:else if active === 'audit'}
					{@render PageTitle('审计', '最近的权限变更和安全相关事件。')}
					<section class="panel">{@render AuditTable(inventory.audit, (event) => (auditDetail = event))}</section>
				{:else if active === 'diagnostics'}
					{@render PageTitle('诊断', '运行时依赖和上游健康检查。')}
					<div class="grid"><div class="metric"><span>Postgres</span><strong>{ready?.checks.postgres ? '正常' : '异常'}</strong></div><div class="metric"><span>Redis</span><strong>{ready?.checks.redis ? '正常' : '异常'}</strong></div><div class="metric"><span>环境</span><strong>{diagnostics?.environment}</strong></div><div class="metric"><span>LiteLLM</span><strong>{diagnostics?.litellm_version}</strong></div></div>
					{@render UpstreamTable(inventory.upstreams, healthResults, modelLabel, checkUpstream, setUpstreamState, deleteUpstream)}
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

{#snippet PageTitle(title: string, subtitle: string)}
	<div class="page-header">
		<div>
			<h1>{title}</h1>
			<p>{subtitle}</p>
		</div>
	</div>
{/snippet}

{#snippet AuditTable(rows: AuditEvent[], onDetail: (event: AuditEvent) => void)}
	<div class="table-wrap">
		<table>
			<thead><tr><th>时间</th><th>动作</th><th>资源</th><th>结果</th><th>详情</th></tr></thead>
			<tbody>
				{#each rows as event}
					<tr>
						<td>{new Date(event.created_at).toLocaleString()}</td>
						<td>{event.action}</td>
						<td>{event.resource_type}<br /><span class="muted">{short(event.resource_id)}</span></td>
						<td><StateBadge value={event.outcome} /></td>
						<td><button class="secondary icon-button" type="button" onclick={() => onDetail(event)}>查看</button></td>
					</tr>
				{:else}
					<tr><td colspan="5" class="empty">暂无审计事件。</td></tr>
				{/each}
			</tbody>
		</table>
	</div>
{/snippet}

{#snippet UsageTable(rows: Inventory['usage'], subjectLabel: (id: string | null | undefined) => string, projectLabel: (id: string | null | undefined) => string)}
	<div class="table-wrap">
		<table>
			<thead><tr><th>模型</th><th>用户</th><th>项目</th><th>请求数</th><th>输入</th><th>输出</th><th>总计</th><th>成功</th><th>失败</th></tr></thead>
			<tbody>
				{#each rows as row}
					<tr><td>{row.model_alias ?? '无'}</td><td>{subjectLabel(row.subject_id)}</td><td>{projectLabel(row.project_id)}</td><td>{row.request_count}</td><td>{row.prompt_tokens}</td><td>{row.completion_tokens}</td><td>{row.total_tokens}</td><td>{row.success_count}</td><td>{row.failure_count}</td></tr>
				{:else}
					<tr><td colspan="9" class="empty">这个时间范围内暂无用量数据。</td></tr>
				{/each}
			</tbody>
		</table>
	</div>
{/snippet}

{#snippet AnalyticsBucketTable(rows: Inventory['analyticsBuckets'], maxTokens: number)}
	<div class="table-wrap">
		<table>
			<thead><tr><th>时间</th><th>压力</th><th>请求</th><th>Token</th><th>缓存</th><th>成功 / 失败</th><th>延迟</th><th>TTFT</th><th>流时长</th><th>vLLM</th></tr></thead>
			<tbody>
				{#each rows as row}
					<tr>
						<td>{new Date(row.bucket_start).toLocaleString()}</td>
						<td><div class="bar-track"><span style={`width: ${Math.max(4, Math.round((row.total_tokens / maxTokens) * 100))}%`}></span></div></td>
						<td>{row.request_count}</td>
						<td>{row.total_tokens}<br /><span class="muted">入 {row.prompt_tokens} / 出 {row.completion_tokens}</span></td>
						<td>{row.cached_tokens}</td>
						<td>{row.success_count} / {row.failure_count}</td>
						<td>{msLabel(row.avg_latency_ms)}</td>
						<td>{msLabel(row.avg_ttft_ms)}</td>
						<td>{msLabel(row.avg_stream_duration_ms)}</td>
						<td>{row.vllm_metrics_count ? `${row.vllm_metrics_count} 条` : '无上游指标'}<br /><span class="muted">queue {msLabel(row.avg_queue_ms)} · prefill {msLabel(row.avg_prefill_ms)} · decode {msLabel(row.avg_decode_ms)} · KV {ratioLabel(row.avg_kv_cache_usage)}</span></td>
					</tr>
				{:else}
					<tr><td colspan="10" class="empty">这个时间范围内暂无趋势数据。</td></tr>
				{/each}
			</tbody>
		</table>
	</div>
{/snippet}

{#snippet AnalyticsDrilldownTable(rows: Inventory['analyticsDrilldown'])}
	<div class="table-wrap">
		<table>
			<thead><tr><th>维度</th><th>请求</th><th>Token</th><th>缓存</th><th>成功 / 失败</th><th>延迟</th><th>TTFT</th><th>Retry / Fallback</th><th>vLLM</th></tr></thead>
			<tbody>
				{#each rows as row}
					<tr>
						<td><strong>{row.dimension_label}</strong><br /><span class="muted">{short(row.dimension_id)}</span></td>
						<td>{row.request_count}</td>
						<td>{row.total_tokens}<br /><span class="muted">入 {row.prompt_tokens} / 出 {row.completion_tokens}</span></td>
						<td>{row.cached_tokens}</td>
						<td>{row.success_count} / {row.failure_count}</td>
						<td>{msLabel(row.avg_latency_ms)}</td>
						<td>{msLabel(row.avg_ttft_ms)}</td>
						<td>{row.retry_count} / {row.fallback_count}<br /><span class="muted">fallback token {row.fallback_tokens}</span></td>
						<td>{row.vllm_metrics_count ? `${row.vllm_metrics_count} 条` : '无上游指标'}</td>
					</tr>
				{:else}
					<tr><td colspan="9" class="empty">暂无 drilldown 数据。</td></tr>
				{/each}
			</tbody>
		</table>
	</div>
{/snippet}

{#snippet CopyValue(label: string, value: string, key: string)}
	<div class="copy-value">
		<span>{label}</span>
		<code>{value}</code>
		<button class="secondary icon-button" type="button" onclick={() => copyText(value, key)} aria-label={`复制 ${label}`}>
			<Copy size={14} />
			{copiedItem === key ? '已复制' : '复制'}
		</button>
	</div>
{/snippet}

{#snippet UpstreamTable(
	rows: Inventory['upstreams'],
	healthResults: Record<string, UpstreamHealth | string>,
	modelLabel: (id: string | null | undefined) => string,
	onCheck: (id: string) => void,
	onState: (id: string, state: ResourceState) => void,
	onDelete: (upstream: Inventory['upstreams'][number]) => void
)}
	<section class="panel">
		<h2>上游端点</h2>
		<div class="table-wrap">
			<table>
				<thead><tr><th>名称</th><th>模型</th><th>Base URL</th><th>状态</th><th>密钥</th><th>健康</th><th>操作</th></tr></thead>
				<tbody>
					{#each rows as upstream}
						<tr>
							<td>{upstream.name}<br /><span class="muted">{short(upstream.id)}</span></td>
							<td>{modelLabel(upstream.model_alias_id)}</td>
							<td>{upstream.base_url}<br /><span class="muted">{upstream.health_path}</span></td>
							<td><StateBadge value={upstream.state} /></td>
							<td><StateBadge value={upstream.has_api_key} tone="accent" /></td>
							<td>
								{#if typeof healthResults[upstream.id] === 'string'}
									<span class="muted">{healthResults[upstream.id]}</span>
								{:else if healthResults[upstream.id]}
									<StateBadge value={(healthResults[upstream.id] as UpstreamHealth).health.status_code} tone={(healthResults[upstream.id] as UpstreamHealth).health.ok ? 'success' : 'danger'} />
								{:else}
									<span class="muted">未检查</span>
								{/if}
							</td>
							<td class="actions"><button class="secondary" type="button" onclick={() => onCheck(upstream.id)}>检查</button><button class="secondary" type="button" onclick={() => onState(upstream.id, upstream.state === 'active' ? 'disabled' : 'active')}>{upstream.state === 'active' ? '禁用' : '启用'}</button><button class="danger" type="button" onclick={() => onDelete(upstream)}>删除</button></td>
						</tr>
					{:else}
						<tr><td colspan="7" class="empty">还没有配置上游端点。</td></tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>
{/snippet}
