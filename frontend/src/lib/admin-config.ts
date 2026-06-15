import {
	Activity,
	BookOpen,
	Database,
	Gauge,
	KeyRound,
	Network,
	Route,
	Shield,
	Trophy,
	UserPlus,
	Users
} from 'lucide-svelte';
import type { Project, ProjectMembership, Subject, TeamMembership } from '$lib/api/types';
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
	usagePreview: 5
} as const;

export const sections: Section[] = [
	{ id: 'diagnostics', label: '诊断', group: '运行', icon: Database },
	{ id: 'models', label: '模型', group: '配置', icon: BookOpen },
	{ id: 'upstreams', label: '上游', group: '配置', icon: Network },
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

export const navGroups = Array.from(new Set(sections.map((section) => section.group)));

export const employeeIdPattern = /^[A-Za-z]\d{8}$/;

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

export function subjectDisplay(subject: Subject): string {
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

// ---- 依赖 inventory/profile 的 label 函数(接收 inventory 作为参数,保持纯函数语义) ----

export type LabelContext = {
	subjects: Subject[];
	managedSubjectCandidates?: Subject[];
	selfSubjectId?: string;
	selfSubject?: Subject | null;
	projects: Project[];
	keys: { id: string; name: string; key_prefix: string }[];
	models: { id: string; alias: string }[];
	teams: { id: string; name: string }[];
};

export function subjectLabel(
	id: string | null | undefined,
	ctx: LabelContext
): string {
	const subject =
		ctx.subjects.find((item) => item.id === id) ??
		(ctx.managedSubjectCandidates ?? []).find((item) => item.id === id) ??
		(ctx.selfSubjectId && ctx.selfSubjectId === id ? (ctx.selfSubject ?? undefined) : undefined);
	return subject ? subjectDisplay(subject) : short(id);
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
	return ctx.projects.find((item) => item.id === id)?.name ?? short(id);
}

export function keyLabel(
	id: string | null | undefined,
	ctx: LabelContext
): string {
	const key = ctx.keys.find((item) => item.id === id);
	return key ? `${key.name} (${key.key_prefix})` : short(id);
}

export function modelLabel(
	id: string | null | undefined,
	ctx: LabelContext
): string {
	return ctx.models.find((item) => item.id === id)?.alias ?? short(id);
}

export function teamLabel(
	id: string | null | undefined,
	ctx: LabelContext
): string {
	return ctx.teams.find((item) => item.id === id)?.name ?? short(id);
}

export function filteredSubjects(query: string, subjects: Subject[]): Subject[] {
	const needle = query.trim().toLowerCase();
	if (!needle) return subjects;
	return subjects.filter((subject) =>
		[subject.name, subject.login_username ?? '', subject.notes ?? ''].some((value) =>
			value.toLowerCase().includes(needle)
		)
	);
}

export function subjectOptions(query: string, subjects: Subject[]): Subject[] {
	return filteredSubjects(query, subjects)
		.toSorted((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN'))
		.slice(0, PAGE_SIZE.selectOptions);
}

export function toDateTimeLocal(date: Date): string {
	const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
	return local.toISOString().slice(0, 16);
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
