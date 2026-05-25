<script lang="ts">
	import { onMount } from 'svelte';
	import {
		Activity,
		BookOpen,
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
		ReadyStatus,
		RegisterResponse,
		ResourceState,
		RouterPolicy,
		SubjectType,
		UpstreamHealth,
		UsageRankingRow
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
		{ id: 'overview', label: 'Overview', group: 'Operate', icon: Activity },
		{ id: 'diagnostics', label: 'Diagnostics', group: 'Operate', icon: Database },
		{ id: 'models', label: 'Models', group: 'Configure', icon: BookOpen },
		{ id: 'upstreams', label: 'Upstreams', group: 'Configure', icon: Network },
		{ id: 'router', label: 'Router Commands', group: 'Configure', icon: Terminal },
		{ id: 'subjects', label: 'Subjects', group: 'Access', icon: Users },
		{ id: 'projects', label: 'Projects', group: 'Access', icon: Route },
		{ id: 'keys', label: 'Gateway Keys', group: 'Access', icon: KeyRound },
		{ id: 'teams', label: 'Teams', group: 'Access', icon: UserPlus },
		{ id: 'entitlements', label: 'Entitlements', group: 'Policy', icon: Shield },
		{ id: 'rate', label: 'Rate Limits', group: 'Policy', icon: Gauge },
		{ id: 'usage', label: 'Usage', group: 'Evidence', icon: Activity },
		{ id: 'ranking', label: 'Ranking', group: 'Evidence', icon: Trophy },
		{ id: 'audit', label: 'Audit', group: 'Evidence', icon: Shield }
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
	let rankingLimit = $state(20);
	let rankingModel = $state('');
	let auditDetail = $state<AuditEvent | null>(null);

	let subjectForm = $state({ name: '', type: 'user' as SubjectType, notes: '' });
	let loginForm = $state({ username: '', password: '' });
	let registerForm = $state({ username: '', password: '' });
	let ownKeyForm = $state({ name: 'personal-key' });
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
		name: '',
		worker_urls: '',
		policy: 'consistent_hash' as RouterPolicy,
		host: '0.0.0.0',
		port: 18001,
		extra_args: '{}'
	});

	const api = $derived(new AdminApiClient('', sessionToken));
	const isAdmin = $derived(Boolean(profile?.subject.is_admin));
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

	onMount(() => {
		sessionToken = loadStoredSessionToken();
		rememberSession = Boolean(sessionToken);
		void refreshReady();
		if (sessionToken) void loadProfile(true);
	});

	async function loginAccount(fromStorage = false) {
		if (!loginForm.username.trim() || !loginForm.password) {
			pageError = 'Username and password are required.';
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
			}
		});
	}

	async function registerAccount() {
		if (!registerForm.username.trim() || registerForm.password.length < 8) {
			pageError = 'Username is required and password must be at least 8 characters.';
			return;
		}
		await run(async () => {
			const response = await new AdminApiClient().post<RegisterResponse>('/auth/register', registerForm);
			sessionToken = response.session_token;
			profile = response.profile;
			plaintextKey = response.gateway_key.plaintext_key;
			connected = true;
			persistSessionToken(sessionToken, rememberSession);
			registerForm = { username: '', password: '' };
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
			}
		});
	}

	function disconnect() {
		sessionToken = '';
		profile = null;
		connected = false;
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
				routerConfigs,
				ratePolicies,
				usage,
				ranking,
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
				api.get<Inventory['routerConfigs']>('/admin/router-command-configs'),
				api.get<Inventory['ratePolicies']>('/admin/rate-policies'),
				api.get<Inventory['usage']>('/admin/usage/summary', { start: usageStart, end: usageEnd }),
				api.get<Inventory['ranking']>('/admin/usage/ranking', { start: usageStart, end: usageEnd, model: rankingModel, limit: rankingLimit }),
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
				routerConfigs,
				ratePolicies,
				usage,
				ranking,
				audit
			};
		});
	}

	async function createSubject() {
		await run(async () => {
			await api.post('/admin/subjects', clean({ ...subjectForm }));
			subjectForm = { name: '', type: 'user', notes: '' };
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
			ownKeyForm = { name: 'personal-key' };
			profile = await api.get<AuthProfile>('/auth/me');
		});
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
			pageError = cidrCheck.message ?? 'Invalid CIDR list';
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

	async function editModelCidrs(model: Inventory['models'][number]) {
		const current = model.ip_allowlist_cidrs.join('\n');
		const value = window.prompt(
			'CIDR allowlist. Leave empty to allow all IPs.',
			model.ip_policy_mode === 'allowlist' ? current : ''
		);
		if (value === null) return;
		const trimmed = value.trim();
		if (!trimmed) {
			await patchModel(model.id, { ip_policy_mode: 'all_pass', ip_allowlist_cidrs: [] });
			return;
		}
		const cidrCheck = validateCidrList(trimmed);
		if (!cidrCheck.ok) {
			pageError = cidrCheck.message ?? 'Invalid CIDR list';
			return;
		}
		await patchModel(model.id, {
			ip_policy_mode: 'allowlist',
			ip_allowlist_cidrs: parseCidrList(trimmed)
		});
	}

	async function createUpstream() {
		const urlCheck = validateHttpUrl(upstreamForm.base_url, 'Base URL');
		if (!urlCheck.ok) {
			pageError = urlCheck.message ?? 'Invalid URL';
			return;
		}
		if (!upstreamForm.health_path.startsWith('/')) {
			pageError = 'Health path must start with /.';
			return;
		}
		await run(async () => {
			await api.post(
				'/admin/upstreams',
				clean({
					...upstreamForm,
					extra_headers: parseJsonObject(upstreamForm.extra_headers, 'Extra headers')
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
		healthResults[id] = 'checking';
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
		const portCheck = validatePort(Number(routerForm.port));
		if (!portCheck.ok) {
			pageError = portCheck.message ?? 'Invalid port';
			return;
		}
		const worker_urls = routerForm.worker_urls
			.split(/\n/)
			.map((item) => item.trim())
			.filter(Boolean);
		if (!worker_urls.length || worker_urls.some((url) => !validateHttpUrl(url, 'Worker URL').ok)) {
			pageError = 'Every worker URL must start with http:// or https://.';
			return;
		}
		await run(async () => {
			await api.post('/admin/router-command-configs', {
				model_alias_id: routerForm.model_alias_id,
				name: routerForm.name,
				worker_urls,
				policy: routerForm.policy,
				host: routerForm.host,
				port: Number(routerForm.port),
				extra_args: parseJsonObject(routerForm.extra_args, 'Extra args')
			});
			routerForm = {
				model_alias_id: '',
				name: '',
				worker_urls: '',
				policy: 'consistent_hash',
				host: '0.0.0.0',
				port: 18001,
				extra_args: '{}'
			};
			await refreshAll();
		});
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
		return 'Unexpected error';
	}

	function short(id: string | null | undefined): string {
		if (!id) return 'none';
		return id.length > 12 ? `${id.slice(0, 8)}…` : id;
	}

	function subjectLabel(id: string | null | undefined): string {
		return inventory.subjects.find((item) => item.id === id)?.name ?? short(id);
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

	function scopeOptions(scope: string) {
		if (scope === 'subject') return inventory.subjects.map((item) => ({ id: item.id, label: item.name }));
		if (scope === 'project') return inventory.projects.map((item) => ({ id: item.id, label: item.name }));
		return inventory.keys.map((item) => ({ id: item.id, label: `${item.name} (${item.key_prefix})` }));
	}
</script>

{#if !connected}
	<div class="app">
		<aside class="sidebar">
			<div class="brand">
				<strong>LLM Gateway</strong>
				<span>Account access</span>
			</div>
		</aside>
		<main class="main">
			<section class="content">
				<div class="split" style="align-items: start;">
				<div class="panel">
					<h1>Sign in</h1>
					<p>Use your gateway account.</p>
					{#if ready}
						<div class="actions">
							<StateBadge value={ready.ok ? 'ready' : 'not_ready'} tone={ready.ok ? 'success' : 'danger'} />
							<span class="muted">Postgres {ready.checks.postgres ? 'ok' : 'down'} · Redis {ready.checks.redis ? 'ok' : 'down'}</span>
						</div>
					{/if}
					<label>
						Username
						<input bind:value={loginForm.username} autocomplete="username" />
					</label>
					<label>
						Password
						<input type="password" bind:value={loginForm.password} autocomplete="current-password" onkeydown={(event) => event.key === 'Enter' && loginAccount()} />
					</label>
					<label style="display: flex; grid-template-columns: auto 1fr; align-items: center;">
						<input type="checkbox" bind:checked={rememberSession} style="width: auto;" />
						Remember on this device
					</label>
					{#if pageError}<div class="error">{pageError}</div>{/if}
					<div class="actions">
						<button type="button" onclick={() => loginAccount()} disabled={loading}>{loading ? 'Signing in' : 'Sign in'}</button>
						<button class="secondary" type="button" onclick={refreshReady}>Refresh readiness</button>
					</div>
				</div>
				<div class="panel">
					<h1>Register</h1>
					<p>New users join <code>guest</code> and receive a gateway key immediately.</p>
					<label>Username<input bind:value={registerForm.username} autocomplete="username" /></label>
					<label>Password<input type="password" bind:value={registerForm.password} autocomplete="new-password" /></label>
					<div class="actions">
						<button type="button" onclick={registerAccount} disabled={loading}>Create account</button>
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
				<span>{diagnostics?.environment ?? 'environment'} · LiteLLM {diagnostics?.litellm_version ?? 'unknown'}</span>
			</div>
			{#if !isAdmin}
				<nav class="nav-group" aria-label="Account">
					<div class="nav-group-title">Account</div>
					<button class="active nav-button" type="button"><span>My access</span><KeyRound size={16} /></button>
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
					<span class="muted">Postgres {ready?.checks.postgres ? 'ok' : 'down'} · Redis {ready?.checks.redis ? 'ok' : 'down'}</span>
				</div>
				<div class="actions">
					<button class="secondary" type="button" onclick={refreshAll} disabled={loading}>{loading ? 'Working' : 'Refresh'}</button>
					<button class="secondary" type="button" onclick={disconnect}>Sign out</button>
				</div>
			</div>
			<section class="content">
				{#if pageError}<div class="error">{pageError}</div>{/if}

				{#if !isAdmin}
					<div class="page-header"><div><h1>My access</h1><p>{profile?.subject.login_username ?? profile?.subject.name}</p></div></div>
					<div class="grid">
						<div class="metric"><span>Teams</span><strong>{profile?.teams.join(', ') || 'none'}</strong></div>
						<div class="metric"><span>Models</span><strong>{profile?.models.length ?? 0}</strong></div>
						<div class="metric"><span>Keys</span><strong>{profile?.keys.length ?? 0}</strong></div>
					</div>
					<section class="panel">
						<h2>Available models</h2>
						<div class="table-wrap"><table><thead><tr><th>Model alias</th></tr></thead><tbody>{#each profile?.models ?? [] as model}<tr><td>{model}</td></tr>{:else}<tr><td>No models granted yet.</td></tr>{/each}</tbody></table></div>
					</section>
					<section class="panel">
						<h2>Gateway keys</h2>
						<div class="form-grid"><label>New key name<input bind:value={ownKeyForm.name} /></label><button type="button" onclick={issueOwnKey}>Issue key</button></div>
						<div class="table-wrap"><table><thead><tr><th>Name</th><th>Prefix</th><th>State</th></tr></thead><tbody>{#each profile?.keys ?? [] as key}<tr><td>{key.name}</td><td><code>{key.key_prefix}</code></td><td><StateBadge value={key.state} /></td></tr>{:else}<tr><td colspan="3">No keys yet.</td></tr>{/each}</tbody></table></div>
					</section>
				{:else if active === 'overview'}
					<div class="page-header">
						<div>
							<h1>Overview</h1>
							<p>Runtime status, recent pressure, and latest privileged changes.</p>
						</div>
					</div>
					<div class="grid">
						<div class="metric"><span>Requests</span><strong>{totals.requests}</strong></div>
						<div class="metric"><span>Total tokens</span><strong>{totals.total}</strong></div>
						<div class="metric"><span>Prompt tokens</span><strong>{totals.prompt}</strong></div>
						<div class="metric"><span>Completion tokens</span><strong>{totals.completion}</strong></div>
						<div class="metric"><span>Success / failure</span><strong>{totals.success} / {totals.failure}</strong></div>
					</div>
					<div class="split">
						<section class="panel">
							<h2>Inventory</h2>
							<div class="grid">
								<div class="metric"><span>Subjects</span><strong>{inventory.subjects.length}</strong></div>
								<div class="metric"><span>Projects</span><strong>{inventory.projects.length}</strong></div>
								<div class="metric"><span>Keys</span><strong>{inventory.keys.length}</strong></div>
								<div class="metric"><span>Models</span><strong>{inventory.models.length}</strong></div>
							</div>
						</section>
						<section class="panel">
							<h2>Recent audit</h2>
							{@render AuditTable(inventory.audit.slice(0, 6), (event) => (auditDetail = event))}
						</section>
					</div>
				{:else if active === 'models'}
					{@render PageTitle('Model aliases', 'Downstream model names, LiteLLM mapping, capabilities, and model-level IP policy.')}
					<section class="panel">
						<h2>Create model alias</h2>
						<div class="form-grid">
							<label>Alias<input bind:value={modelForm.alias} placeholder="dev-model" /></label>
							<label>Upstream model<input bind:value={modelForm.upstream_model_name} /></label>
							<label>LiteLLM model<input bind:value={modelForm.litellm_model} placeholder="openai/model-name" /></label>
							<label>IP policy<select bind:value={modelForm.ip_policy_mode}><option value="all_pass">all_pass</option><option value="allowlist">allowlist</option></select></label>
							<label>CIDRs<textarea bind:value={modelForm.ip_allowlist_cidrs} placeholder="10.0.0.0/8"></textarea></label>
							<label>Notes<input bind:value={modelForm.notes} /></label>
						</div>
						<div class="actions">
							<label style="display:flex; width:auto; align-items:center;"><input type="checkbox" bind:checked={modelForm.supports_streaming} style="width:auto;" /> Streaming</label>
							<label style="display:flex; width:auto; align-items:center;"><input type="checkbox" bind:checked={modelForm.supports_tools} style="width:auto;" /> Tools</label>
							<label style="display:flex; width:auto; align-items:center;"><input type="checkbox" bind:checked={modelForm.supports_reasoning} style="width:auto;" /> Reasoning</label>
							<button type="button" onclick={createModel}>Create alias</button>
						</div>
					</section>
					<section class="panel">
						<h2>Aliases</h2>
						<div class="table-wrap">
							<table>
								<thead><tr><th>Alias</th><th>LiteLLM</th><th>State</th><th>IP policy</th><th>Capabilities</th><th>Actions</th></tr></thead>
								<tbody>
									{#each inventory.models as model}
										<tr>
											<td><strong>{model.alias}</strong><br /><span class="muted">{model.upstream_model_name}</span></td>
											<td>{model.litellm_model}</td>
											<td><StateBadge value={model.state} /></td>
											<td>{model.ip_policy_mode}<br /><span class="muted">{model.ip_allowlist_cidrs.join(', ') || 'no CIDRs'}</span></td>
											<td><StateBadge value={model.supports_streaming} tone="accent" /> <StateBadge value={model.supports_tools} tone="accent" /> <StateBadge value={model.supports_reasoning} tone="accent" /></td>
											<td class="actions"><button class="secondary" type="button" onclick={() => editModelCidrs(model)}>Edit CIDRs</button><button class="secondary" type="button" onclick={() => patchModel(model.id, { state: model.state === 'active' ? 'disabled' : 'active' })}>{model.state === 'active' ? 'Disable' : 'Activate'}</button></td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					</section>
				{:else if active === 'upstreams'}
					{@render PageTitle('Upstreams', 'OpenAI-compatible upstreams or vLLM Router pools behind model aliases.')}
					<section class="panel">
						<h2>Create upstream</h2>
						<div class="form-grid">
							<label>Model<select bind:value={upstreamForm.model_alias_id}><option value="">Select model</option>{#each inventory.models as model}<option value={model.id}>{model.alias}</option>{/each}</select></label>
							<label>Name<input bind:value={upstreamForm.name} /></label>
							<label>Base URL<input bind:value={upstreamForm.base_url} placeholder="http://host:8000/v1" /></label>
							<label>Health path<input bind:value={upstreamForm.health_path} /></label>
							<label>API key ref<input bind:value={upstreamForm.api_key_ref} /></label>
							<label>API key value<input type="password" bind:value={upstreamForm.api_key_value} /></label>
							<label>Extra headers<textarea bind:value={upstreamForm.extra_headers}></textarea></label>
							<button type="button" onclick={createUpstream}>Create upstream</button>
						</div>
					</section>
					{@render UpstreamTable(inventory.upstreams, healthResults, modelLabel, checkUpstream, setUpstreamState)}
				{:else if active === 'subjects'}
					{@render PageTitle('Subjects', 'Gateway-managed human users and service accounts.')}
					<section class="panel">
						<h2>Create subject</h2>
						<div class="form-grid">
							<label>Name<input bind:value={subjectForm.name} /></label>
							<label>Type<select bind:value={subjectForm.type}><option value="user">user</option><option value="service">service</option></select></label>
							<label>Notes<input bind:value={subjectForm.notes} /></label>
							<button type="button" onclick={createSubject}>Create subject</button>
						</div>
					</section>
					<section class="panel">
						<h2>Subjects</h2>
						<div class="table-wrap"><table><thead><tr><th>Name</th><th>Type</th><th>State</th><th>Notes</th><th>Actions</th></tr></thead><tbody>{#each inventory.subjects as subject}<tr><td>{subject.name}<br /><span class="muted">{short(subject.id)}</span></td><td>{subject.type}</td><td><StateBadge value={subject.state} /></td><td>{subject.notes}</td><td class="actions"><button class="secondary" type="button" onclick={() => patchSubject(subject.id, { notes: prompt('Notes', subject.notes ?? '') ?? subject.notes })}>Edit notes</button><button class="secondary" type="button" onclick={() => setSubjectState(subject.id, subject.state === 'active' ? 'disabled' : 'active')}>{subject.state === 'active' ? 'Disable' : 'Activate'}</button></td></tr>{/each}</tbody></table></div>
					</section>
				{:else if active === 'projects'}
					{@render PageTitle('Projects', 'Usage attribution and project membership.')}
					<div class="split">
						<section class="panel">
							<h2>Create project</h2>
							<div class="form-grid">
								<label>Name<input bind:value={projectForm.name} /></label>
								<label>Owner<select bind:value={projectForm.owner_subject_id}><option value="">None</option>{#each inventory.subjects as subject}<option value={subject.id}>{subject.name}</option>{/each}</select></label>
								<label>Notes<input bind:value={projectForm.notes} /></label>
								<button type="button" onclick={createProject}>Create project</button>
							</div>
						</section>
						<section class="panel">
							<h2>Add membership</h2>
							<div class="form-grid">
								<label>Project<select bind:value={membershipForm.project_id}><option value="">Project</option>{#each inventory.projects as project}<option value={project.id}>{project.name}</option>{/each}</select></label>
								<label>Subject<select bind:value={membershipForm.subject_id}><option value="">Subject</option>{#each inventory.subjects as subject}<option value={subject.id}>{subject.name}</option>{/each}</select></label>
								<label>Role<input bind:value={membershipForm.role} /></label>
								<button type="button" onclick={createMembership}>Add member</button>
							</div>
						</section>
					</div>
					<section class="panel"><h2>Projects</h2><div class="table-wrap"><table><thead><tr><th>Name</th><th>Owner</th><th>State</th><th>Notes</th><th>Actions</th></tr></thead><tbody>{#each inventory.projects as project}<tr><td>{project.name}<br /><span class="muted">{short(project.id)}</span></td><td>{subjectLabel(project.owner_subject_id)}</td><td><StateBadge value={project.state} /></td><td>{project.notes}</td><td><button class="secondary" type="button" onclick={() => patchProject(project.id, { notes: prompt('Notes', project.notes ?? '') ?? project.notes })}>Edit notes</button></td></tr>{/each}</tbody></table></div></section>
					<section class="panel"><h2>Memberships</h2><div class="table-wrap"><table><thead><tr><th>Project</th><th>Subject</th><th>Role</th></tr></thead><tbody>{#each inventory.memberships as membership}<tr><td>{projectLabel(membership.project_id)}</td><td>{subjectLabel(membership.subject_id)}</td><td>{membership.role}</td></tr>{/each}</tbody></table></div></section>
				{:else if active === 'keys'}
					{@render PageTitle('Gateway keys', 'Issue, rotate, and revoke gateway-owned keys.')}
					<section class="panel"><h2>Issue key</h2><div class="form-grid"><label>Subject<select bind:value={keyForm.subject_id}><option value="">Subject</option>{#each inventory.subjects as subject}<option value={subject.id}>{subject.name}</option>{/each}</select></label><label>Project<select bind:value={keyForm.project_id}><option value="">Project</option>{#each inventory.projects as project}<option value={project.id}>{project.name}</option>{/each}</select></label><label>Name<input bind:value={keyForm.name} /></label><button type="button" onclick={issueKey}>Issue key</button></div></section>
					<section class="panel"><h2>Keys</h2><div class="table-wrap"><table><thead><tr><th>Name</th><th>Prefix</th><th>Subject</th><th>Project</th><th>State</th><th>Actions</th></tr></thead><tbody>{#each inventory.keys as key}<tr><td>{key.name}</td><td><code>{key.key_prefix}</code></td><td>{subjectLabel(key.subject_id)}</td><td>{projectLabel(key.project_id)}</td><td><StateBadge value={key.state} /></td><td><button class="secondary" type="button" onclick={() => setKeyState(key.id, key.state === 'active' ? 'disabled' : 'active')}>{key.state === 'active' ? 'Disable' : 'Activate'}</button></td></tr>{/each}</tbody></table></div></section>
				{:else if active === 'teams'}
					{@render PageTitle('Teams', 'Self-service users inherit model access from all active teams they belong to.')}
					<div class="split">
						<section class="panel"><h2>Create team</h2><div class="form-grid"><label>Name<input bind:value={teamForm.name} /></label><label>Notes<input bind:value={teamForm.notes} /></label><button type="button" onclick={createTeam}>Create team</button></div></section>
						<section class="panel"><h2>Add user to team</h2><div class="form-grid"><label>Team<select bind:value={teamMembershipForm.team_id}><option value="">Team</option>{#each inventory.teams as team}<option value={team.id}>{team.name}</option>{/each}</select></label><label>Subject<select bind:value={teamMembershipForm.subject_id}><option value="">Subject</option>{#each inventory.subjects as subject}<option value={subject.id}>{subject.name}</option>{/each}</select></label><label>Role<input bind:value={teamMembershipForm.role} /></label><button type="button" onclick={createTeamMembership}>Add membership</button></div></section>
					</div>
					<section class="panel"><h2>Grant model to team</h2><div class="form-grid"><label>Model<select bind:value={modelTeamGrantForm.model_alias_id}><option value="">Model</option>{#each inventory.models as model}<option value={model.id}>{model.alias}</option>{/each}</select></label><label>Team<select bind:value={modelTeamGrantForm.team_id}><option value="">Team</option>{#each inventory.teams as team}<option value={team.id}>{team.name}</option>{/each}</select></label><button type="button" onclick={createModelTeamGrant}>Grant model</button></div></section>
					<section class="panel"><h2>Teams</h2><div class="table-wrap"><table><thead><tr><th>Name</th><th>State</th><th>Builtin</th><th>Notes</th><th>Actions</th></tr></thead><tbody>{#each inventory.teams as team}<tr><td>{team.name}<br /><span class="muted">{short(team.id)}</span></td><td><StateBadge value={team.state} /></td><td><StateBadge value={team.is_builtin} tone="accent" /></td><td>{team.notes}</td><td><button class="secondary" type="button" onclick={() => patchTeam(team.id, { state: team.state === 'active' ? 'disabled' : 'active' })}>{team.state === 'active' ? 'Disable' : 'Activate'}</button></td></tr>{/each}</tbody></table></div></section>
					<section class="panel"><h2>Memberships</h2><div class="table-wrap"><table><thead><tr><th>Team</th><th>Subject</th><th>Role</th><th>State</th><th>Actions</th></tr></thead><tbody>{#each inventory.teamMemberships as membership}<tr><td>{teamLabel(membership.team_id)}</td><td>{subjectLabel(membership.subject_id)}</td><td>{membership.role}</td><td><StateBadge value={membership.state} /></td><td><button class="secondary" type="button" onclick={() => setTeamMembershipState(membership.id, membership.state === 'active' ? 'disabled' : 'active')}>{membership.state === 'active' ? 'Disable' : 'Activate'}</button></td></tr>{/each}</tbody></table></div></section>
					<section class="panel"><h2>Model grants</h2><div class="table-wrap"><table><thead><tr><th>Model</th><th>Team</th><th>State</th><th>Actions</th></tr></thead><tbody>{#each inventory.modelTeamGrants as grant}<tr><td>{modelLabel(grant.model_alias_id)}</td><td>{teamLabel(grant.team_id)}</td><td><StateBadge value={grant.state} /></td><td><button class="secondary" type="button" onclick={() => setModelTeamGrantState(grant.id, grant.state === 'active' ? 'disabled' : 'active')}>{grant.state === 'active' ? 'Disable' : 'Activate'}</button></td></tr>{/each}</tbody></table></div></section>
				{:else if active === 'entitlements'}
					{@render PageTitle('Entitlements', 'Grant model access to projects, subjects, or individual gateway keys.')}
					<section class="panel"><h2>Create entitlement</h2><div class="form-grid"><label>Model<select bind:value={entitlementForm.model_alias_id}><option value="">Model</option>{#each inventory.models as model}<option value={model.id}>{model.alias}</option>{/each}</select></label><label>Scope<select bind:value={entitlementForm.scope} onchange={() => (entitlementForm.scope_id = '')}><option value="project">project</option><option value="subject">subject</option><option value="key">key</option></select></label><label>Scope target<select bind:value={entitlementForm.scope_id}><option value="">Target</option>{#each scopeOptions(entitlementForm.scope) as option}<option value={option.id}>{option.label}</option>{/each}</select></label><button type="button" onclick={createEntitlement}>Grant access</button></div></section>
					<section class="panel"><h2>Entitlements</h2><div class="table-wrap"><table><thead><tr><th>Model</th><th>Scope</th><th>State</th><th>Actions</th></tr></thead><tbody>{#each inventory.entitlements as entitlement}<tr><td>{modelLabel(entitlement.model_alias_id)}</td><td>{entitlement.project_id ? `project: ${projectLabel(entitlement.project_id)}` : entitlement.subject_id ? `subject: ${subjectLabel(entitlement.subject_id)}` : `key: ${keyLabel(entitlement.gateway_key_id)}`}</td><td><StateBadge value={entitlement.state} /></td><td><button class="secondary" type="button" onclick={() => setEntitlementState(entitlement.id, entitlement.state === 'active' ? 'disabled' : 'active')}>{entitlement.state === 'active' ? 'Disable' : 'Activate'}</button></td></tr>{/each}</tbody></table></div></section>
				{:else if active === 'rate'}
					{@render PageTitle('Rate limits', 'DB-backed request-per-minute and active-concurrency policies.')}
					<section class="panel"><h2>Create rate policy</h2><p>Effective limits are the minimum active policy across key, subject, project, and environment defaults.</p><div class="form-grid"><label>Scope<select bind:value={rateForm.scope} onchange={() => (rateForm.scope_id = '')}><option value="key">key</option><option value="subject">subject</option><option value="project">project</option></select></label><label>Target<select bind:value={rateForm.scope_id}><option value="">Target</option>{#each scopeOptions(rateForm.scope) as option}<option value={option.id}>{option.label}</option>{/each}</select></label><label>RPM<input type="number" min="0" bind:value={rateForm.requests_per_minute} /></label><label>Concurrency<input type="number" min="0" bind:value={rateForm.concurrency_limit} /></label><button type="button" onclick={createRatePolicy}>Create policy</button></div></section>
					<section class="panel"><h2>Policies</h2><div class="table-wrap"><table><thead><tr><th>Scope</th><th>Target</th><th>RPM</th><th>Concurrency</th><th>State</th><th>Actions</th></tr></thead><tbody>{#each inventory.ratePolicies as policy}<tr><td>{policy.scope}</td><td>{policy.scope === 'subject' ? subjectLabel(policy.scope_id) : policy.scope === 'project' ? projectLabel(policy.scope_id) : keyLabel(policy.scope_id)}</td><td>{policy.requests_per_minute ?? 'inherit'}</td><td>{policy.concurrency_limit ?? 'inherit'}</td><td><StateBadge value={policy.state} /></td><td><button class="secondary" type="button" onclick={() => setRateState(policy.id, policy.state === 'active' ? 'disabled' : 'active')}>{policy.state === 'active' ? 'Disable' : 'Activate'}</button></td></tr>{/each}</tbody></table></div></section>
				{:else if active === 'router'}
					{@render PageTitle('Router commands', 'Generate vLLM Router commands; MVP does not start or supervise router processes.')}
					<section class="panel"><h2>Create command config</h2><div class="form-grid"><label>Model<select bind:value={routerForm.model_alias_id}><option value="">Model</option>{#each inventory.models as model}<option value={model.id}>{model.alias}</option>{/each}</select></label><label>Name<input bind:value={routerForm.name} /></label><label>Policy<select bind:value={routerForm.policy}><option value="consistent_hash">consistent_hash</option><option value="cache_aware">cache_aware</option></select></label><label>Host<input bind:value={routerForm.host} /></label><label>Port<input type="number" bind:value={routerForm.port} /></label><label>Worker URLs<textarea bind:value={routerForm.worker_urls} placeholder="http://gpu-a:8000&#10;http://gpu-b:8000"></textarea></label><label>Extra args<textarea bind:value={routerForm.extra_args}></textarea></label><button type="button" onclick={createRouterConfig}>Create config</button></div></section>
					<section class="panel"><h2>Generated commands</h2>{#each inventory.routerConfigs as item}<div class="panel compact"><div class="toolbar"><div><strong>{item.config.name}</strong><p>{modelLabel(item.config.model_alias_id)} · {item.config.policy} · {item.config.host}:{item.config.port}</p></div></div><CommandBlock command={item.command} /></div>{:else}<p class="empty">No router command configs yet.</p>{/each}</section>
				{:else if active === 'usage'}
					{@render PageTitle('Usage', 'Grouped request and token pressure by model, subject, and project.')}
					<section class="panel"><div class="form-grid"><label>Start<input type="datetime-local" bind:value={usageStart} /></label><label>End<input type="datetime-local" bind:value={usageEnd} /></label><label>Model filter<select bind:value={modelFilter}><option value="">All</option>{#each inventory.models as model}<option value={model.alias}>{model.alias}</option>{/each}</select></label><label>Subject filter<select bind:value={subjectFilter}><option value="">All</option>{#each inventory.subjects as subject}<option value={subject.id}>{subject.name}</option>{/each}</select></label><label>Project filter<select bind:value={projectFilter}><option value="">All</option>{#each inventory.projects as project}<option value={project.id}>{project.name}</option>{/each}</select></label><button type="button" onclick={refreshAll}>Query</button></div></section>
					<div class="grid"><div class="metric"><span>Requests</span><strong>{totals.requests}</strong></div><div class="metric"><span>Total tokens</span><strong>{totals.total}</strong></div><div class="metric"><span>Success</span><strong>{totals.success}</strong></div><div class="metric"><span>Failure</span><strong>{totals.failure}</strong></div></div>
					<section class="panel"><h2>Summary rows</h2>{@render UsageTable(usageRows, subjectLabel, projectLabel)}</section>
				{:else if active === 'ranking'}
					{@render PageTitle('Ranking', 'Top users by token usage within a time range.')}
					<section class="panel"><div class="form-grid"><label>Start<input type="datetime-local" bind:value={usageStart} /></label><label>End<input type="datetime-local" bind:value={usageEnd} /></label><label>Model filter<select bind:value={rankingModel}><option value="">All</option>{#each inventory.models as model}<option value={model.alias}>{model.alias}</option>{/each}</select></label><label>Top N<input type="number" bind:value={rankingLimit} min="1" max="100" /></label><button type="button" onclick={refreshAll}>Query</button></div></section>
					<section class="panel"><div class="table-wrap"><table><thead><tr><th>#</th><th>User</th><th>Requests</th><th>Prompt tokens</th><th>Completion tokens</th><th>Total tokens</th></tr></thead><tbody>{#each inventory.ranking as row, i}<tr><td>{i + 1}</td><td>{row.login_username || row.subject_name}</td><td>{row.request_count}</td><td>{row.prompt_tokens}</td><td>{row.completion_tokens}</td><td>{row.total_tokens}</td></tr>{:else}<tr><td colspan="6" class="empty">No usage data.</td></tr>{/each}</tbody></table></div></section>
				{:else if active === 'audit'}
					{@render PageTitle('Audit', 'Recent privileged changes and security-significant events.')}
					<section class="panel">{@render AuditTable(inventory.audit, (event) => (auditDetail = event))}</section>
				{:else if active === 'diagnostics'}
					{@render PageTitle('Diagnostics', 'Runtime dependencies and upstream health checks.')}
					<div class="grid"><div class="metric"><span>Postgres</span><strong>{ready?.checks.postgres ? 'OK' : 'Down'}</strong></div><div class="metric"><span>Redis</span><strong>{ready?.checks.redis ? 'OK' : 'Down'}</strong></div><div class="metric"><span>Environment</span><strong>{diagnostics?.environment}</strong></div><div class="metric"><span>LiteLLM</span><strong>{diagnostics?.litellm_version}</strong></div></div>
					{@render UpstreamTable(inventory.upstreams, healthResults, modelLabel, checkUpstream, setUpstreamState)}
				{/if}
			</section>
		</main>
	</div>
{/if}

{#if auditDetail}
	<div class="modal-backdrop" role="presentation">
		<section class="modal" aria-label="Audit detail">
			<header><h2>{auditDetail.action}</h2><p>{auditDetail.resource_type} · {auditDetail.created_at}</p></header>
			<JsonViewer value={auditDetail} />
			<footer><button class="secondary" type="button" onclick={() => (auditDetail = null)}>Close</button></footer>
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
			<thead><tr><th>Time</th><th>Action</th><th>Resource</th><th>Outcome</th><th>Detail</th></tr></thead>
			<tbody>
				{#each rows as event}
					<tr>
						<td>{new Date(event.created_at).toLocaleString()}</td>
						<td>{event.action}</td>
						<td>{event.resource_type}<br /><span class="muted">{short(event.resource_id)}</span></td>
						<td><StateBadge value={event.outcome} /></td>
						<td><button class="secondary icon-button" type="button" onclick={() => onDetail(event)}>View</button></td>
					</tr>
				{:else}
					<tr><td colspan="5" class="empty">No audit events.</td></tr>
				{/each}
			</tbody>
		</table>
	</div>
{/snippet}

{#snippet UsageTable(rows: Inventory['usage'], subjectLabel: (id: string | null | undefined) => string, projectLabel: (id: string | null | undefined) => string)}
	<div class="table-wrap">
		<table>
			<thead><tr><th>Model</th><th>Subject</th><th>Project</th><th>Requests</th><th>Prompt</th><th>Completion</th><th>Total</th><th>Success</th><th>Failure</th></tr></thead>
			<tbody>
				{#each rows as row}
					<tr><td>{row.model_alias ?? 'none'}</td><td>{subjectLabel(row.subject_id)}</td><td>{projectLabel(row.project_id)}</td><td>{row.request_count}</td><td>{row.prompt_tokens}</td><td>{row.completion_tokens}</td><td>{row.total_tokens}</td><td>{row.success_count}</td><td>{row.failure_count}</td></tr>
				{:else}
					<tr><td colspan="9" class="empty">No usage rows for this window.</td></tr>
				{/each}
			</tbody>
		</table>
	</div>
{/snippet}

{#snippet UpstreamTable(
	rows: Inventory['upstreams'],
	healthResults: Record<string, UpstreamHealth | string>,
	modelLabel: (id: string | null | undefined) => string,
	onCheck: (id: string) => void,
	onState: (id: string, state: ResourceState) => void
)}
	<section class="panel">
		<h2>Upstreams</h2>
		<div class="table-wrap">
			<table>
				<thead><tr><th>Name</th><th>Model</th><th>Base URL</th><th>State</th><th>Secret</th><th>Health</th><th>Actions</th></tr></thead>
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
									<span class="muted">not checked</span>
								{/if}
							</td>
							<td class="actions"><button class="secondary" type="button" onclick={() => onCheck(upstream.id)}>Check</button><button class="secondary" type="button" onclick={() => onState(upstream.id, upstream.state === 'active' ? 'disabled' : 'active')}>{upstream.state === 'active' ? 'Disable' : 'Activate'}</button></td>
						</tr>
					{:else}
						<tr><td colspan="7" class="empty">No upstreams configured.</td></tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>
{/snippet}
