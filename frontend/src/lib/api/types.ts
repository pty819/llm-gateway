export type SubjectType = 'user' | 'service';
export type ResourceState = 'active' | 'disabled';
export type IPPolicyMode = 'all_pass' | 'allowlist';
export type RouterPolicy = 'consistent_hash' | 'cache_aware';
export type EndpointFamily = 'openai_chat' | 'anthropic_messages';
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

export type RouterCommandConfig = Timestamped & {
	id: string;
	model_alias_id: string;
	name: string;
	worker_urls: string[];
	policy: RouterPolicy;
	host: string;
	port: number;
	extra_args: Record<string, unknown>;
};

export type RouterCommandConfigResponse = {
	config: RouterCommandConfig;
	command: string;
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
	routerConfigs: RouterCommandConfigResponse[];
	ratePolicies: RatePolicy[];
	usage: UsageSummaryRow[];
	audit: AuditEvent[];
};
