export type ValidationResult = {
	ok: boolean;
	message?: string;
};

export function validateCidrList(value: string): ValidationResult {
	const items = splitLines(value);
	for (const item of items) {
		if (!isCidr(item)) return { ok: false, message: `CIDR 不合法: ${item}` };
	}
	return { ok: true };
}

export function parseCidrList(value: string): string[] {
	return splitLines(value);
}

export function validateHttpUrl(value: string, label = 'URL'): ValidationResult {
	if (!value.trim()) return { ok: false, message: `请填写${label}` };
	try {
		const url = new URL(value);
		if (url.protocol !== 'http:' && url.protocol !== 'https:') {
			return { ok: false, message: `${label}必须以 http:// 或 https:// 开头` };
		}
		return { ok: true };
	} catch {
		return { ok: false, message: `${label}不合法` };
	}
}

export function parseJsonObject(value: string, label = 'JSON'): Record<string, unknown> {
	if (!value.trim()) return {};
	const parsed = JSON.parse(value) as unknown;
	if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
		throw new Error(`${label}必须是 JSON 对象`);
	}
	return parsed as Record<string, unknown>;
}

export function maskSecrets(value: unknown): unknown {
	if (Array.isArray(value)) return value.map(maskSecrets);
	if (!value || typeof value !== 'object') return value;
	const result: Record<string, unknown> = {};
	for (const [key, item] of Object.entries(value)) {
		if (/key|token|secret|password|authorization/i.test(key)) result[key] = item ? '••••••••' : item;
		else result[key] = maskSecrets(item);
	}
	return result;
}

function splitLines(value: string): string[] {
	return value
		.split(/[\n,]/)
		.map((item) => item.trim())
		.filter(Boolean);
}

function isCidr(value: string): boolean {
	const [ip, prefix] = value.split('/');
	if (!ip || prefix === undefined) return false;
	const prefixNumber = Number(prefix);
	if (!Number.isInteger(prefixNumber)) return false;
	if (ip.includes(':')) return prefixNumber >= 0 && prefixNumber <= 128 && /^[0-9a-fA-F:]+$/.test(ip);
	if (prefixNumber < 0 || prefixNumber > 32) return false;
	const parts = ip.split('.');
	return parts.length === 4 && parts.every((part) => /^\d+$/.test(part) && Number(part) >= 0 && Number(part) <= 255);
}
