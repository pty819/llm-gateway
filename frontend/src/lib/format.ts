/** 数字声部格式化:全站 token/请求数/RPM/并发等数值的统一呈现。
 * 规则(设计稿 V4):
 * - |v| >= 100_000 走紧凑格式(123.4K / 12.3M / 1.2B,保留 1 位小数,去尾零)
 * - |v| >= 10_000 走千分位(12,345)
 * - 其余原样输出(整数不带小数)
 * 非有限数(含 null/undefined)返回 `—`(设计稿统一空值文案)。 */
export function fmtNumber(value: number | null | undefined): string {
	if (value === null || value === undefined || !Number.isFinite(value)) return '—';
	const abs = Math.abs(value);
	if (abs >= 1_000_000_000) return compact(value / 1_000_000_000, 'B');
	if (abs >= 1_000_000) return compact(value / 1_000_000, 'M');
	if (abs >= 100_000) return compact(value / 1_000, 'K');
	if (abs >= 10_000) return value.toLocaleString('en-US');
	return Number.isInteger(value) ? String(value) : String(Math.round(value * 10) / 10);
}

function compact(value: number, suffix: string): string {
	const rounded = Math.round(value * 10) / 10;
	const text = Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
	return `${text}${suffix}`;
}

/** 百分比(0–1 比率输入),保留 1 位小数;空值返回 `—`。 */
export function fmtPercent(ratio: number | null | undefined): string {
	if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return '—';
	return `${(ratio * 100).toFixed(1)}%`;
}
