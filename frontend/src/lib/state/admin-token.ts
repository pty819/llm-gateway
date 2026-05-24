const STORAGE_KEY = 'llm-gateway-admin-token';

export function loadStoredAdminToken(): string {
	if (typeof localStorage === 'undefined') return '';
	return localStorage.getItem(STORAGE_KEY) ?? '';
}

export function persistAdminToken(token: string, remember: boolean): void {
	if (typeof localStorage === 'undefined') return;
	if (remember) localStorage.setItem(STORAGE_KEY, token);
	else localStorage.removeItem(STORAGE_KEY);
}

export function clearStoredAdminToken(): void {
	if (typeof localStorage === 'undefined') return;
	localStorage.removeItem(STORAGE_KEY);
}
