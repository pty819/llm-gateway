import type { AdminApiClient } from '$lib/api/client';
import type {
	Diagnostics,
	GatewayKey,
	HealthCheckConfig,
	Inventory,
	PaginatedResponse,
	Project,
	ProjectMembership,
	RuntimeMetricsSnapshot,
	Subject,
	Team,
	TeamMembership,
	UpstreamHealth
} from '$lib/api/types';
import { errorMessage } from '$lib/admin-config';

/**
 * Inventory store: owns admin inventory lists, live realtime metrics
 * (SSE consumer), upstream health probe results, health-check config,
 * and the diagnostics payload. Mirrors the block that previously lived
 * inline in routes/+page.svelte.
 *
 * Page-level concerns (forms, own/managed usage, profile/admin branching)
 * stay in the page and drive this store through setInventory / setDiagnostics.
 */
export function createInventoryStore(
	getApi: () => AdminApiClient,
	getSessionToken: () => string
) {
	let inventory = $state<Inventory>(emptyInventory());
	let realtime = $state<RuntimeMetricsSnapshot | null>(null);
	let realtimeStatus = $state('未连接');
	let realtimeLocked = $state(false);
	let realtimeAbort: AbortController | null = null;
	let healthResults = $state<Record<string, UpstreamHealth | string>>({});
	let healthCheckConfig = $state<HealthCheckConfig | null>(null);
	let healthCheckToggling = $state(false);
	let diagnostics = $state<Diagnostics | null>(null);

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

	/**
	 * Fetch the full admin inventory (subjects/projects/keys/models/teams/
	 * upstreams/entitlements/rate-policies/audit + health-check config) in
	 * parallel. Mirrors the admin branch of the original refreshAll.
	 * Usage/ranking/analytics totals are left untouched (refreshed by their
	 * own actions on the page). Throws on error so the caller can wrap with
	 * the shared loading/error handler.
	 */
	async function refreshInventory(): Promise<void> {
		const api = getApi();
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
	}

	/** Replace usage/ranking/analytics slices without touching the list data. */
	function patchInventory(patch: Partial<Inventory>): void {
		inventory = { ...inventory, ...patch };
	}

	/** Reset everything back to the empty default (used on logout). */
	function resetInventory(): void {
		inventory = emptyInventory();
	}

	function startRealtimeStream(): void {
		stopRealtimeStream();
		if (!getSessionToken() || typeof window === 'undefined') return;
		const controller = new AbortController();
		realtimeAbort = controller;
		realtimeStatus = '连接中';
		void consumeRealtimeStream(controller);
	}

	function stopRealtimeStream(): void {
		if (realtimeAbort) {
			realtimeAbort.abort();
			realtimeAbort = null;
		}
		realtimeStatus = '未连接';
	}

	async function consumeRealtimeStream(controller: AbortController): Promise<void> {
		try {
			const response = await fetch('/admin/realtime/stream?window_seconds=10&interval_seconds=1', {
				headers: { 'x-session-token': getSessionToken() },
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

	function consumeRealtimeEvent(block: string): void {
		const data = block
			.split('\n')
			.filter((line) => line.startsWith('data:'))
			.map((line) => line.slice(5).trimStart())
			.join('\n');
		if (!data) return;
		realtime = JSON.parse(data) as RuntimeMetricsSnapshot;
	}

	/** Toggle the upstream health-check patrol via the admin API. */
	async function toggleHealthCheck(): Promise<void> {
		if (!healthCheckConfig || healthCheckToggling) return;
		const next = !healthCheckConfig.enabled;
		healthCheckToggling = true;
		try {
			healthCheckConfig = await getApi().setHealthCheckConfig(next);
		} finally {
			healthCheckToggling = false;
		}
	}

	/** Probe one upstream's /models health endpoint. */
	async function checkUpstream(id: string): Promise<void> {
		healthResults[id] = '检查中';
		try {
			healthResults[id] = await getApi().get<UpstreamHealth>(`/admin/upstreams/${id}/health`);
		} catch (error) {
			healthResults[id] = errorMessage(error);
		}
	}

	/** Fetch the diagnostics payload (environment etc.) after admin auth. */
	async function fetchDiagnostics(): Promise<void> {
		diagnostics = await getApi().get<Diagnostics>('/admin/diagnostics');
	}

	return {
		get inventory() {
			return inventory;
		},
		get realtime() {
			return realtime;
		},
		get realtimeStatus() {
			return realtimeStatus;
		},
		get realtimeLocked() {
			return realtimeLocked;
		},
		set realtimeLocked(v: boolean) {
			realtimeLocked = v;
		},
		get healthResults() {
			return healthResults;
		},
		get healthCheckConfig() {
			return healthCheckConfig;
		},
		get healthCheckToggling() {
			return healthCheckToggling;
		},
		get diagnostics() {
			return diagnostics;
		},
		refreshInventory,
		patchInventory,
		resetInventory,
		startRealtimeStream,
		stopRealtimeStream,
		toggleHealthCheck,
		checkUpstream,
		fetchDiagnostics
	};
}

export type InventoryStore = ReturnType<typeof createInventoryStore>;
