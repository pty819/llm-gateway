export type SubjectType = 'user' | 'service';
export type ResourceState = 'active' | 'disabled';
export type IPPolicyMode = 'all_pass' | 'allowlist';
export type EndpointFamily = 'openai_chat' | 'openai_responses' | 'anthropic_messages';
export type RequestOutcome =
	| 'success'
	| 'auth_failure'
	| 'policy_denial'
	| 'rate_limited'
	| 'adapter_failure'
	| 'upstream_failure'
	| 'client_cancelled';

export type Timestamped = {
	created_at: string;
	updated_at: string;
};

export type Subject = Timestamped & {
	id: string;
	name: string;
	type: SubjectType;
	state: ResourceState;
	notes: string | null;
	login_username: string | null;
	is_admin: boolean;
	requires_real_name?: boolean;
};

export type Project = Timestamped & {
	id: string;
	name: string;
	state: ResourceState;
	owner_subject_id: string | null;
	notes: string | null;
};

export type ProjectMembership = Timestamped & {
	id: string;
	project_id: string;
	subject_id: string;
	subject_name?: string | null;
	subject_login_username?: string | null;
	role: string;
};

export type GatewayKey = Timestamped & {
	id: string;
	subject_id: string;
	project_id: string;
	name: string;
	key_prefix: string;
	key_hash: null;
	state: ResourceState;
	expires_at: string | null;
};

export type GatewayKeyCreateResponse = {
	key: GatewayKey;
	plaintext_key: string;
};

export type ModelAlias = Timestamped & {
	id: string;
	alias: string;
	upstream_model_name: string;
	litellm_model: string;
	state: ResourceState;
	supports_streaming: boolean;
	supports_tools: boolean;
	supports_reasoning: boolean;
	sticky_ttl_seconds: number;
	ip_policy_mode: IPPolicyMode;
	ip_allowlist_cidrs: string[];
	notes: string | null;
};

export type ModelEntitlement = Timestamped & {
	id: string;
	subject_id: string | null;
	project_id: string | null;
	gateway_key_id: string | null;
	model_alias_id: string;
	state: ResourceState;
};

export type Team = Timestamped & {
	id: string;
	name: string;
	state: ResourceState;
	notes: string | null;
	is_builtin: boolean;
};

export type TeamMembership = Timestamped & {
	id: string;
	team_id: string;
	subject_id: string;
	subject_name?: string | null;
	subject_login_username?: string | null;
	role: string;
	state: ResourceState;
};

export type ModelTeamGrant = Timestamped & {
	id: string;
	model_alias_id: string;
	team_id: string;
	state: ResourceState;
};

export type UpstreamTarget = Timestamped & {
	id: string;
	model_alias_id: string;
	name: string;
	base_url: string;
	metrics_url: string | null;
	api_key_ref: string | null;
	api_key_value: null;
	has_api_key: boolean;
	health_path: string;
	state: ResourceState;
	extra_headers: Record<string, string>;
};

export type UpstreamHealth = {
	upstream: UpstreamTarget;
	health: {
		ok: boolean;
		status_code: number;
		url: string;
	};
};

export type RuntimeMetricsUpstream = {
	upstream_id: string;
	upstream_name: string;
	model_alias: string;
	tokens_per_second: number | null;
	recent_tokens: number | null;
	active_connections: number;
	vllm?: VllmMetricsSnapshot;
};

export type RuntimeMetricsSnapshot = {
	generated_at: string;
	window_seconds: number;
	metrics_cache_seconds?: number;
	total_tokens_per_second: number | null;
	total_recent_tokens: number | null;
	active_connections: number;
	vllm: {
		configured_upstreams: number;
		observed_upstreams: number;
		ok_upstreams: number;
		ignored_upstreams: number;
		running: number | null;
		waiting: number | null;
		swapped: number | null;
		tokens_per_second: number | null;
		max_kv_cache_usage: number | null;
		router: VllmRouterMetricsSummary;
	};
	upstreams: RuntimeMetricsUpstream[];
};

export type VllmMetricsSnapshot = {
	ok: boolean;
	kind: 'vllm' | 'vllm_router' | 'unknown';
	metrics_url: string;
	scraped_at: string;
	error?: string;
	running: number | null;
	waiting: number | null;
	swapped: number | null;
	kv_cache_usage: number | null;
	cpu_cache_usage: number | null;
	prefix_cache_hit_ratio: number | null;
	prompt_tokens_total: number | null;
	generation_tokens_total: number | null;
	tokens_total: number | null;
	tokens_per_second: number | null;
	router: VllmRouterMetricsSummary | null;
};

export type VllmRouterMetricsSummary = {
	observed_upstreams?: number;
	requests_total?: number | null;
	request_errors_total?: number | null;
	processed_requests_total?: number | null;
	active_workers?: number | null;
	healthy_workers?: number | null;
	worker_load?: number | null;
	running_requests?: number | null;
	max_load?: number | null;
	min_load?: number | null;
	cache_hits_total?: number | null;
	cache_misses_total?: number | null;
	cache_hit_ratio?: number | null;
};

export type RatePolicy = Timestamped & {
	id: string;
	scope: 'key' | 'subject' | 'project' | string;
	scope_id: string;
	requests_per_minute: number | null;
	concurrency_limit: number | null;
	state: ResourceState;
};

export type UsageSummaryRow = {
	model_alias: string | null;
	subject_id: string | null;
	project_id: string | null;
	request_count: number;
	prompt_tokens: number;
	completion_tokens: number;
	total_tokens: number;
	success_count: number;
	failure_count: number;
};

export type UsageTotalsRow = {
	request_count: number;
	prompt_tokens: number;
	completion_tokens: number;
	total_tokens: number;
	cached_tokens: number;
	success_count: number;
	failure_count: number;
	avg_latency_ms: number | null;
	avg_ttft_ms: number | null;
	avg_stream_duration_ms: number | null;
	retry_count: number;
	fallback_count: number;
	fallback_tokens: number;
	avg_queue_ms: number | null;
	avg_prefill_ms: number | null;
	avg_decode_ms: number | null;
	avg_kv_cache_usage: number | null;
	vllm_metrics_count: number;
};

export type OwnUsageSummary = {
	start: string | null;
	end: string | null;
	request_count: number;
	prompt_tokens: number;
	completion_tokens: number;
	total_tokens: number;
	success_count: number;
	failure_count: number;
};

export type UsageRankingRow = {
	subject_id: string;
	login_username: string | null;
	subject_name: string;
	request_count: number;
	prompt_tokens: number;
	completion_tokens: number;
	total_tokens: number;
};

export type AnalyticsBucketRow = {
	bucket_start: string;
	request_count: number;
	prompt_tokens: number;
	completion_tokens: number;
	total_tokens: number;
	cached_tokens: number;
	success_count: number;
	failure_count: number;
	avg_latency_ms: number | null;
	avg_ttft_ms: number | null;
	avg_stream_duration_ms: number | null;
	retry_count: number;
	fallback_count: number;
	fallback_tokens: number;
	avg_queue_ms: number | null;
	avg_prefill_ms: number | null;
	avg_decode_ms: number | null;
	avg_kv_cache_usage: number | null;
	vllm_metrics_count: number;
};

export type AnalyticsDrilldownRow = Omit<AnalyticsBucketRow, 'bucket_start'> & {
	dimension_id: string | null;
	dimension_label: string;
};

export type AuditEvent = {
	id: string;
	created_at: string;
	actor_subject_id: string | null;
	action: string;
	resource_type: string;
	resource_id: string | null;
	outcome: string;
	detail: Record<string, unknown>;
};

export type ReadyStatus = {
	ok: boolean;
	checks: {
		postgres: boolean;
		redis: boolean;
	};
};

export type Diagnostics = {
	app_name: string;
	environment: string;
	litellm_version: string;
};

export type AuthProfile = {
	subject: Subject;
	teams: string[];
	models: string[];
	keys: GatewayKey[];
	managed: {
		projects: Array<{ project: Project; membership: ProjectMembership }>;
		teams: Array<{ team: Team; membership: TeamMembership }>;
	};
};

export type LoginResponse = {
	session_token: string;
	session_expires_at: string;
	profile: AuthProfile;
};

export type RegisterResponse = LoginResponse & {
	gateway_key: GatewayKeyCreateResponse;
	project: Project;
};

export type PaginatedResponse<T> = {
	items: T[];
	total: number;
	limit: number;
	offset: number;
};

export type ApiError = {
	status: number;
	message: string;
	detail?: unknown;
};

export type Inventory = {
	subjects: Subject[];
	projects: Project[];
	memberships: ProjectMembership[];
	keys: GatewayKey[];
	models: ModelAlias[];
	entitlements: ModelEntitlement[];
	teams: Team[];
	teamMemberships: TeamMembership[];
	modelTeamGrants: ModelTeamGrant[];
	upstreams: UpstreamTarget[];
	ratePolicies: RatePolicy[];
	usage: UsageSummaryRow[];
	usageTotals: UsageTotalsRow | null;
	ranking: UsageRankingRow[];
	analyticsBuckets: AnalyticsBucketRow[];
	analyticsDrilldown: AnalyticsDrilldownRow[];
	audit: AuditEvent[];
};

export interface ManagedRankingRow {
	subject_id: string;
	subject_name: string;
	login_username: string | null;
	request_count: number;
	prompt_tokens: number;
	completion_tokens: number;
	total_tokens: number;
	success_count: number;
	failure_count: number;
}

export interface SkillSummary {
	id: string;
	owner_subject_id: string;
	owner_name: string | null;
	slug: string;
	name: string;
	summary: string | null;
	state: string;
	latest_version: string | null;
	updated_at: string | null;
}

export interface SkillTeamGrantSummary {
	id: string;
	skill_id: string;
	team_id: string;
	state: string;
}

export interface Paginated<T> {
	items: T[];
	total: number;
	page?: number;
	size?: number;
}
