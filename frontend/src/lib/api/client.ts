import type {
	ApiError,
	Paginated,
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
