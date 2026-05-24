export type ValidationResult = {
	ok: boolean;
	message?: string;
};

export function validateCidrList(value: string): ValidationResult {
	const items = splitLines(value);
	for (const item of items) {
		if (!isCidr(item)) return { ok: false, message: `Invalid CIDR: ${item}` };
	}
	return { ok: true };
}

export function parseCidrList(value: string): string[] {
	return splitLines(value);
}

export function validateHttpUrl(value: string, label = 'URL'): ValidationResult {
	if (!value.trim()) return { ok: false, message: `${label} is required` };
	try {
		const url = new URL(value);
		if (url.protocol !== 'http:' && url.protocol !== 'https:') {
			return { ok: false, message: `${label} must start with http:// or https://` };
		}
		return { ok: true };
	} catch {
		return { ok: false, message: `${label} is invalid` };
	}
}

export function validatePort(port: number): ValidationResult {
	if (!Number.isInteger(port) || port < 1 || port > 65535) {
		return { ok: false, message: 'Port must be an integer from 1 to 65535' };
	}
	return { ok: true };
}

export function parseJsonObject(value: string, label = 'JSON'): Record<string, unknown> {
	if (!value.trim()) return {};
	const parsed = JSON.parse(value) as unknown;
	if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
		throw new Error(`${label} must be a JSON object`);
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
