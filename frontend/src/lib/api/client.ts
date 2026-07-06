import type {
	ApiError,
	HealthCheckConfig,
	McpConfigInput,
	McpDetail,
	McpSummary,
	McpTeamGrantSummary,
	Paginated,
	SkillDetail,
	SkillSummary,
	SkillTeamGrantSummary
} from './types';

type QueryValue = string | number | boolean | null | undefined;

export class AdminApiClient {
	adminToken = '';
	sessionToken = '';

	constructor(adminToken = '', sessionToken = '') {
		this.adminToken = adminToken;
		this.sessionToken = sessionToken;
	}

	async get<T>(path: string, params?: Record<string, QueryValue>): Promise<T> {
		return this.request<T>('GET', withQuery(path, params));
	}

	async post<T>(path: string, body: unknown): Promise<T> {
		return this.request<T>('POST', path, body);
	}

	async patch<T>(path: string, body: unknown): Promise<T> {
		return this.request<T>('PATCH', path, body);
	}

	async delete<T>(path: string, params?: Record<string, QueryValue>): Promise<T> {
		return this.request<T>('DELETE', withQuery(path, params));
	}

	async listMySkills(): Promise<Paginated<SkillSummary>> {
		return this.get('/auth/registry/skills');
	}

	async uploadSkill(
		form: {
			slug: string;
			name: string;
			version: string;
			summary?: string;
			description?: string;
			notes?: string;
		},
		file: File
	): Promise<{ skill: SkillSummary }> {
		const fd = new FormData();
		fd.append('file', file);
		fd.append('slug', form.slug);
		fd.append('name', form.name);
		fd.append('version', form.version);
		if (form.summary) fd.append('summary', form.summary);
		if (form.description) fd.append('description', form.description);
		if (form.notes) fd.append('notes', form.notes);
		return this.post('/auth/registry/skills', fd);
	}

	async listSkillGrants(slug: string): Promise<Paginated<SkillTeamGrantSummary>> {
		return this.get(`/auth/registry/skills/me/${encodeURIComponent(slug)}/grants`);
	}

	async grantSkill(slug: string, teamId: string): Promise<{ grant: SkillTeamGrantSummary }> {
		return this.post(`/auth/registry/skills/me/${encodeURIComponent(slug)}/grants`, {
			team_id: teamId
		});
	}

	async revokeSkillGrant(
		slug: string,
		grantId: string
	): Promise<{ grant: SkillTeamGrantSummary }> {
		return this.patch(
			`/auth/registry/skills/me/${encodeURIComponent(slug)}/grants/${grantId}/state`,
			{ state: 'disabled' }
		);
	}

	async listBrowseSkills(params?: {
		q?: string;
		owner?: string;
		page?: number;
		size?: number;
		sort?: string;
	}): Promise<Paginated<SkillSummary>> {
		return this.get('/auth/registry/skills/browse', params);
	}

	async getSkillDetail(owner: string, slug: string): Promise<SkillDetail> {
		return this.get(
			`/auth/registry/skills/browse/${encodeURIComponent(owner)}/${encodeURIComponent(slug)}`
		);
	}

	async likeSkill(owner: string, slug: string): Promise<{ liked_by_me: boolean; like_count: number }> {
		return this.post(
			`/auth/registry/skills/browse/${encodeURIComponent(owner)}/${encodeURIComponent(slug)}/like`,
			{}
		);
	}

	async unlikeSkill(
		owner: string,
		slug: string
	): Promise<{ liked_by_me: boolean; like_count: number }> {
		return this.delete(
			`/auth/registry/skills/browse/${encodeURIComponent(owner)}/${encodeURIComponent(slug)}/like`
		);
	}

	async downloadSkill(owner: string, slug: string, version = 'latest'): Promise<Blob> {
		const path = withQuery(
			`/auth/registry/skills/browse/${encodeURIComponent(owner)}/${encodeURIComponent(slug)}/download`,
			{ version }
		);
		return this.requestBlob(path);
	}

	async listMyMcps(): Promise<Paginated<McpSummary>> {
		return this.get('/auth/registry/mcps');
	}

	async publishMcp(
		form: {
			slug: string;
			name: string;
			version: string;
			summary?: string;
			description?: string;
			notes?: string;
			readme?: string;
		},
		config: McpConfigInput
	): Promise<{ mcp: McpSummary }> {
		return this.post('/auth/registry/mcps', { ...form, config });
	}

	async listMcpGrants(slug: string): Promise<Paginated<McpTeamGrantSummary>> {
		return this.get(`/auth/registry/mcps/me/${encodeURIComponent(slug)}/grants`);
	}

	async grantMcp(slug: string, teamId: string): Promise<{ grant: McpTeamGrantSummary }> {
		return this.post(`/auth/registry/mcps/me/${encodeURIComponent(slug)}/grants`, {
			team_id: teamId
		});
	}

	async revokeMcpGrant(slug: string, grantId: string): Promise<{ grant: McpTeamGrantSummary }> {
		return this.patch(
			`/auth/registry/mcps/me/${encodeURIComponent(slug)}/grants/${grantId}/state`,
			{ state: 'disabled' }
		);
	}

	async listBrowseMcps(params?: {
		q?: string;
		owner?: string;
		page?: number;
		size?: number;
		sort?: string;
	}): Promise<Paginated<McpSummary>> {
		return this.get('/auth/registry/mcps/browse', params);
	}

	async getMcpDetail(owner: string, slug: string): Promise<McpDetail> {
		return this.get(
			`/auth/registry/mcps/browse/${encodeURIComponent(owner)}/${encodeURIComponent(slug)}`
		);
	}

	async likeMcp(owner: string, slug: string): Promise<{ liked_by_me: boolean; like_count: number }> {
		return this.post(
			`/auth/registry/mcps/browse/${encodeURIComponent(owner)}/${encodeURIComponent(slug)}/like`,
			{}
		);
	}

	async unlikeMcp(
		owner: string,
		slug: string
	): Promise<{ liked_by_me: boolean; like_count: number }> {
		return this.delete(
			`/auth/registry/mcps/browse/${encodeURIComponent(owner)}/${encodeURIComponent(slug)}/like`
		);
	}

	async getHealthCheckConfig(): Promise<HealthCheckConfig> {
		return this.get('/admin/health-check');
	}

	async setHealthCheckConfig(enabled: boolean): Promise<HealthCheckConfig> {
		return this.patch('/admin/health-check', { enabled });
	}

	private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
		const isFormData = typeof FormData !== 'undefined' && body instanceof FormData;
		const response = await fetch(path, {
			method,
			headers: {
				...(this.adminToken ? { 'x-admin-token': this.adminToken } : {}),
				...(this.sessionToken ? { 'x-session-token': this.sessionToken } : {}),
				...(body === undefined || isFormData ? {} : { 'content-type': 'application/json' })
			},
			body:
				body === undefined
					? undefined
					: isFormData
						? (body as FormData)
						: JSON.stringify(body)
		});
		if (!response.ok) {
			throw await toApiError(response);
		}
		return (await response.json()) as T;
	}

	private async requestBlob(path: string): Promise<Blob> {
		const response = await fetch(path, {
			method: 'GET',
			headers: {
				...(this.adminToken ? { 'x-admin-token': this.adminToken } : {}),
				...(this.sessionToken ? { 'x-session-token': this.sessionToken } : {})
			}
		});
		if (!response.ok) {
			throw await toApiError(response);
		}
		return await response.blob();
	}
}

export async function toApiError(response: Response): Promise<ApiError> {
	let payload: unknown;
	try {
		payload = await response.json();
	} catch {
		payload = undefined;
	}
	if (isGatewayError(payload)) {
		return {
			status: response.status,
			message: `${payload.error.type}: ${payload.error.message}`,
			detail: payload
		};
	}
	if (isFastApiDetail(payload)) {
		return {
			status: response.status,
			message: typeof payload.detail === 'string' ? payload.detail : response.statusText,
			detail: payload.detail
		};
	}
	return {
		status: response.status,
		message: response.statusText || `HTTP ${response.status}`,
		detail: payload
	};
}

export function withQuery(path: string, params?: Record<string, QueryValue>): string {
	if (!params) return path;
	const query = new URLSearchParams();
	for (const [key, value] of Object.entries(params)) {
		if (value !== undefined && value !== null && value !== '') query.set(key, String(value));
	}
	const suffix = query.toString();
	return suffix ? `${path}?${suffix}` : path;
}

export function isApiError(error: unknown): error is ApiError {
	return Boolean(error && typeof error === 'object' && 'status' in error && 'message' in error);
}

/** Trigger a browser download for an already-fetched blob. */
export function downloadBlob(blob: Blob, filename: string): void {
	const url = URL.createObjectURL(blob);
	const a = document.createElement('a');
	a.href = url;
	a.download = filename;
	a.click();
	URL.revokeObjectURL(url);
}

function isGatewayError(value: unknown): value is { error: { type: string; message: string } } {
	if (!value || typeof value !== 'object' || !('error' in value)) return false;
	const error = (value as { error: unknown }).error;
	return Boolean(
		error &&
			typeof error === 'object' &&
			'type' in error &&
			'message' in error &&
			typeof (error as { type: unknown }).type === 'string' &&
			typeof (error as { message: unknown }).message === 'string'
	);
}

function isFastApiDetail(value: unknown): value is { detail: unknown } {
	return Boolean(value && typeof value === 'object' && 'detail' in value);
}
