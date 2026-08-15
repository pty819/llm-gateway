import {
	Activity,
	BookOpen,
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
import type { ProjectMembership, Subject, TeamMembership } from '$lib/api/types';
import { isApiError } from '$lib/api/client';

export type Section = {
	id: string;
	label: string;
	group: string;
	icon: typeof Activity;
};

export const PAGE_SIZE = {
	defaultList: 30,
	selectOptions: 20,
	ranking: 50,
	audit: 50,
	usagePreview: 5,
	bucketRibbon: 72
} as const;

export const sections: Section[] = [
	{ id: 'diagnostics', label: '诊断', group: '总览', icon: Database },
	{ id: 'models', label: '模型', group: '配置', icon: BookOpen },
	{ id: 'upstreams', label: '上游', group: '配置', icon: Network },
	{ id: 'subjects', label: '用户', group: '访问控制', icon: Users },
	{ id: 'projects', label: '项目', group: '访问控制', icon: Route },
	{ id: 'keys', label: '网关密钥', group: '访问控制', icon: KeyRound },
	{ id: 'teams', label: '权限组', group: '访问控制', icon: UserPlus },
	{ id: 'skill-market', label: 'Skill 市场', group: '市场', icon: Package },
	{ id: 'mcp-market', label: 'MCP 市场', group: '市场', icon: Plug },
	{ id: 'entitlements', label: '旧授权', group: '治理', icon: Shield },
	{ id: 'rate', label: '限流', group: '治理', icon: Gauge },
	{ id: 'usage', label: '用量', group: '数据', icon: Activity },
	{ id: 'ranking', label: '排行榜', group: '数据', icon: Trophy },
	{ id: 'audit', label: '审计', group: '数据', icon: Shield }
];

export const navGroups = Array.from(new Set(sections.map((section) => section.group)));

export const employeeIdPattern = /^[A-Za-z]\d{8}$/;

export const marketSlugPattern = /^[a-z][a-z0-9-]*$/;

// ---- 纯函数(无状态依赖的 label / 格式化助手) ----

export function short(id: string | null | undefined): string {
	if (!id) return '无';
	return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}

export function msLabel(value: number | null | undefined): string {
	return value === null || value === undefined ? '无数据' : `${Math.round(value)} ms`;
}

export function ratioLabel(value: number | null | undefined): string {
	return value === null || value === undefined ? '无数据' : `${Math.round(value * 100)}%`;
}

export function tokenRateLabel(value: number | null | undefined): string {
	const numeric = Number(value ?? 0);
	const digits = numeric >= 100 ? 0 : 1;
	return `${numeric.toFixed(digits)} token/s`;
}

export function metricsKindLabel(value: string | null | undefined): string {
	if (value === 'vllm') return 'vLLM';
	if (value === 'vllm_router') return 'Router';
	return '未知';
}

export function bytesLabel(value: number): string {
	if (value < 1024) return `${value} B`;
	if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
	return `${(value / 1024 / 1024).toFixed(1)} MiB`;
}

export function subjectTypeLabel(type: string): string {
	return type === 'service' ? '服务账号' : '用户';
}

export function scopeLabel(scope: string): string {
	if (scope === 'subject') return '用户';
	if (scope === 'project') return '项目';
	if (scope === 'key') return '密钥';
	return scope;
}

export function subjectDisplay(subject: Pick<Subject, 'name' | 'login_username'>): string {
	return subject.login_username ? `${subject.name} / ${subject.login_username}` : subject.name;
}

export function matchNeedle(query: string, values: Array<string | null | undefined>): boolean {
	const needle = query.trim().toLowerCase();
	if (!needle) return true;
	return values.some((value) => (value ?? '').toLowerCase().includes(needle));
}

export function pageRows<T>(rows: T[], page: number, size: number): T[] {
	const safePage = Math.min(Math.max(1, page), pageCount(rows, size));
	const start = (safePage - 1) * size;
	return rows.slice(start, start + size);
}

export function pageCount(rows: unknown[], size: number): number {
	return pageCountTotal(rows.length, size);
}

export function pageCountTotal(total: number, size: number): number {
	return Math.max(1, Math.ceil(total / size));
}

// ---- 依赖名称缓存的 label 函数(接收缓存作为参数,保持纯函数语义) ----
// 服务端分页后前端不再持有全量清单;labelCtx 改为按 id 索引的名称缓存,
// 缓存条目来自各列表当前页的随行名称与 /options 轻量端点。

export type SubjectNameEntry = { name: string | null; login_username?: string | null };
export type KeyNameEntry = { name: string; key_prefix: string };

export type LabelContext = {
	subjects: Record<string, SubjectNameEntry>;
	managedSubjectCandidates?: Subject[];
	selfSubjectId?: string;
	selfSubject?: Subject | null;
	projects: Record<string, string>;
	keys: Record<string, KeyNameEntry>;
	models: Record<string, string>;
	teams: Record<string, string>;
};

function subjectEntryLabel(entry: SubjectNameEntry): string {
	return entry.login_username ? `${entry.name} / ${entry.login_username}` : (entry.name ?? '');
}

export function subjectLabel(
	id: string | null | undefined,
	ctx: LabelContext
): string {
	const cached = id ? ctx.subjects[id] : undefined;
	if (cached) return subjectEntryLabel(cached) || short(id);
	const managed =
		ctx.managedSubjectCandidates?.find((item) => item.id === id) ??
		(ctx.selfSubjectId && ctx.selfSubjectId === id ? (ctx.selfSubject ?? undefined) : undefined);
	return managed ? subjectDisplay(managed) : short(id);
}

export function membershipSubjectLabel(
	membership: ProjectMembership | TeamMembership,
	ctx: LabelContext
): string {
	const directName = membership.subject_name?.trim();
	if (directName) {
		return membership.subject_login_username
			? `${directName} / ${membership.subject_login_username}`
			: directName;
	}
	return subjectLabel(membership.subject_id, ctx);
}

export function projectLabel(
	id: string | null | undefined,
	ctx: LabelContext
): string {
	return (id ? ctx.projects[id] : undefined) ?? short(id);
}

export function keyLabel(
	id: string | null | undefined,
	ctx: LabelContext
): string {
	const key = id ? ctx.keys[id] : undefined;
	return key ? `${key.name} (${key.key_prefix})` : short(id);
}

export function modelLabel(
	id: string | null | undefined,
	ctx: LabelContext
): string {
	return (id ? ctx.models[id] : undefined) ?? short(id);
}

export function teamLabel(
	id: string | null | undefined,
	ctx: LabelContext
): string {
	return (id ? ctx.teams[id] : undefined) ?? short(id);
}

export function toDateTimeLocal(date: Date): string {
	const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
	return local.toISOString().slice(0, 16);
}

/**
 * Convert a `datetime-local` input value (browser-local, no offset) into a
 * UTC ISO string with explicit offset for API queries. The backend stores
 * naive-UTC timestamps; sending an unmarked local time would shift the query
 * window by the browser's UTC offset (8h in Shanghai).
 */
export function datetimeLocalToUtcIso(value: string): string {
	if (!value) return '';
	const parsed = new Date(value); // parses datetime-local as browser-local
	return Number.isNaN(parsed.getTime()) ? '' : parsed.toISOString();
}

/**
 * Parse a server timestamp for display. Server datetimes are naive UTC; a bare
 * ISO string would be parsed as browser-local and render 8h off (Shanghai).
 * Values that already carry an offset/Z pass through unchanged.
 */
export function parseServerUtcIso(value: string): Date {
	if (/[zZ]$|[+\-]\d{2}:?\d{2}$/.test(value)) return new Date(value);
	return new Date(`${value}Z`);
}

export function usageRangeForDays(days: number): { start: string; end: string } {
	const end = new Date();
	const start = new Date(end.getTime() - days * 24 * 60 * 60 * 1000);
	return { start: toDateTimeLocal(start), end: toDateTimeLocal(end) };
}

export function defaultUsageRange(): { start: string; end: string } {
	return usageRangeForDays(7);
}

export function inferGatewayBaseUrl(): string {
	if (typeof window === 'undefined') return '';
	return window.location.origin;
}

export function clean<T extends Record<string, unknown>>(value: T): T {
	const result: Record<string, unknown> = {};
	for (const [key, item] of Object.entries(value)) {
		result[key] = item === '' ? null : item;
	}
	return result as T;
}

export function errorMessage(error: unknown): string {
	if (isApiError(error)) return `${error.status}: ${error.message}`;
	if (error instanceof Error) return error.message;
	return '发生未知错误';
}
