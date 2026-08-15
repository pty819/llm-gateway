import type {
	KeyOption,
	ModelAlias,
	ModelEntitlement,
	ModelOption,
	ModelTeamGrant,
	Project,
	ProjectMembership,
	ProjectOption,
	RatePolicy,
	SubjectOption,
	Team,
	TeamMembership,
	TeamOption,
	TeamTokenQuotaRow,
	UsageSummaryRow
} from '$lib/api/types';
import type { KeyNameEntry, LabelContext, SubjectNameEntry } from '$lib/admin-config';

export type NameCache = Pick<
	LabelContext,
	'subjects' | 'projects' | 'keys' | 'models' | 'teams'
>;

export function emptyNameCache(): NameCache {
	return { subjects: {}, projects: {}, keys: {}, models: {}, teams: {} };
}

/** 单条 id→名称写入;返回新对象保证 Svelte 响应式更新。 */
function withEntry(
	target: Record<string, string>,
	id: string | null | undefined,
	name: string | null | undefined
): Record<string, string> {
	if (!id || !name) return target;
	return { ...target, [id]: name };
}

function withEntries(
	target: Record<string, string>,
	pairs: Array<{ id?: string | null; name?: string | null }>
): Record<string, string> {
	let next = target;
	for (const pair of pairs) next = withEntry(next, pair.id, pair.name);
	return next;
}

function withSubjectEntry(
	target: Record<string, SubjectNameEntry>,
	id: string | null | undefined,
	name: string | null | undefined,
	login: string | null | undefined
): Record<string, SubjectNameEntry> {
	if (!id || !name) return target;
	const existing = target[id];
	const merged: SubjectNameEntry = {
		name,
		login_username: login ?? existing?.login_username ?? null
	};
	return { ...target, [id]: merged };
}

/** 把各服务端响应里随行出现的 id/name 对合并进缓存。所有函数纯且不可变。 */

export function mergeSubjectRows(cache: NameCache, rows: SubjectOption[]): NameCache {
	let subjects = cache.subjects;
	for (const row of rows) subjects = withSubjectEntry(subjects, row.id, row.name, row.login_username);
	return { ...cache, subjects };
}

export function mergeSubjectRefs(
	cache: NameCache,
	refs: Array<{
		subject_id?: string | null;
		subject_name?: string | null;
		subject_login_username?: string | null;
	}>
): NameCache {
	let subjects = cache.subjects;
	for (const ref of refs) {
		subjects = withSubjectEntry(subjects, ref.subject_id, ref.subject_name, ref.subject_login_username);
	}
	return { ...cache, subjects };
}

export function mergeOwnerRefs(cache: NameCache, rows: Project[]): NameCache {
	let next = mergeSubjectRefs(
		cache,
		rows.map((row) => ({
			subject_id: row.owner_subject_id,
			subject_name: row.owner_name,
			subject_login_username: row.owner_login_username
		}))
	);
	next = mergeProjects(next, rows.map((row) => ({ id: row.id, name: row.name })));
	return next;
}

export function mergeProjects(cache: NameCache, rows: ProjectOption[]): NameCache {
	return { ...cache, projects: withEntries(cache.projects, rows) };
}

export function mergeProjectRefs(
	cache: NameCache,
	refs: Array<{ project_id?: string | null; project_name?: string | null }>
): NameCache {
	return {
		...cache,
		projects: withEntries(
			cache.projects,
			refs.map((ref) => ({ id: ref.project_id, name: ref.project_name }))
		)
	};
}

export function mergeProjectMembershipRows(cache: NameCache, rows: ProjectMembership[]): NameCache {
	let next = mergeSubjectRefs(cache, rows);
	next = mergeProjectRefs(next, rows);
	return next;
}

export function mergeKeyRows(cache: NameCache, rows: KeyOption[]): NameCache {
	let keys = cache.keys;
	for (const row of rows) {
		if (!row.id) continue;
		keys = { ...keys, [row.id]: { name: row.name, key_prefix: row.key_prefix } };
	}
	return { ...cache, keys };
}

export function mergeKeyRefs(
	cache: NameCache,
	rows: Array<{
		id?: string;
		name?: string;
		key_prefix?: string;
		subject_id?: string | null;
		project_id?: string | null;
		subject_name?: string | null;
		subject_login_username?: string | null;
		project_name?: string | null;
	}>
): NameCache {
	let next = mergeSubjectRefs(cache, rows);
	next = mergeProjectRefs(next, rows);
	let keys = next.keys;
	for (const row of rows) {
		if (!row.id || !row.name || !row.key_prefix) continue;
		keys = { ...keys, [row.id]: { name: row.name, key_prefix: row.key_prefix } };
	}
	return { ...next, keys };
}

export function mergeModelRows(cache: NameCache, rows: ModelOption[] | ModelAlias[]): NameCache {
	return { ...cache, models: withEntries(cache.models, rows) };
}

export function mergeModelRefs(
	cache: NameCache,
	refs: Array<{ model_alias_id?: string | null; model_alias?: string | null }>
): NameCache {
	return {
		...cache,
		models: withEntries(
			cache.models,
			refs.map((ref) => ({ id: ref.model_alias_id, name: ref.model_alias }))
		)
	};
}

export function mergeTeamRows(cache: NameCache, rows: TeamOption[] | Team[]): NameCache {
	return {
		...cache,
		teams: withEntries(
			cache.teams,
			rows.map((row) => ({ id: row.id, name: row.name }))
		)
	};
}

export function mergeTeamRefs(
	cache: NameCache,
	refs: Array<{ team_id?: string | null; team_name?: string | null }>
): NameCache {
	return {
		...cache,
		teams: withEntries(
			cache.teams,
			refs.map((ref) => ({ id: ref.team_id, name: ref.team_name }))
		)
	};
}

export function mergeTeamMembershipRows(cache: NameCache, rows: TeamMembership[]): NameCache {
	let next = mergeSubjectRefs(cache, rows);
	next = mergeTeamRefs(next, rows);
	return next;
}

export function mergeModelTeamGrantRows(cache: NameCache, rows: ModelTeamGrant[]): NameCache {
	let next = mergeModelRefs(cache, rows);
	next = mergeTeamRefs(next, rows);
	return next;
}

export function mergeEntitlementRows(cache: NameCache, rows: ModelEntitlement[]): NameCache {
	let next = mergeModelRefs(cache, rows);
	next = mergeSubjectRefs(next, rows);
	next = mergeProjectRefs(next, rows);
	let keys = next.keys;
	for (const row of rows) {
		if (!row.gateway_key_id || !row.key_name) continue;
		keys = { ...keys, [row.gateway_key_id]: { name: row.key_name, key_prefix: shortId(row.gateway_key_id) } };
	}
	return { ...next, keys };
}

export function mergeRatePolicyRows(cache: NameCache, rows: RatePolicy[]): NameCache {
	const subjectRefs: Array<{ subject_id?: string | null; subject_name?: string | null }> = [];
	const projectRefs: Array<{ project_id?: string | null; project_name?: string | null }> = [];
	const keyRefs: KeyOption[] = [];
	for (const row of rows) {
		if (row.scope === 'subject') subjectRefs.push({ subject_id: row.scope_id, subject_name: row.scope_name });
		else if (row.scope === 'project') projectRefs.push({ project_id: row.scope_id, project_name: row.scope_name });
		else if (row.scope === 'key' && row.scope_name) keyRefs.push({ id: row.scope_id, name: row.scope_name, key_prefix: shortId(row.scope_id) });
	}
	let next = mergeSubjectRefs(cache, subjectRefs);
	next = mergeProjectRefs(next, projectRefs);
	return mergeKeyRows(next, keyRefs);
}

export function mergeUpstreamRows(
	cache: NameCache,
	rows: Array<{ model_alias_id?: string | null; model_alias?: string | null }>
): NameCache {
	return mergeModelRefs(cache, rows);
}

export function mergeQuotaRows(cache: NameCache, rows: TeamTokenQuotaRow[]): NameCache {
	return mergeTeamRefs(
		cache,
		rows.map((row) => ({ team_id: row.team_id, team_name: row.team_name }))
	);
}

export function mergeUsageRows(cache: NameCache, rows: UsageSummaryRow[]): NameCache {
	let next = mergeSubjectRefs(cache, rows);
	next = mergeProjectRefs(next, rows);
	return next;
}

function shortId(id: string): string {
	return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}
