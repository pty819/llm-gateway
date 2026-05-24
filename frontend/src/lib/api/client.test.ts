import { describe, expect, it } from 'vitest';
import { toApiError, withQuery } from './client';

describe('api client helpers', () => {
	it('builds query strings without empty values', () => {
		expect(withQuery('/admin/usage/summary', { start: '2026-01-01', end: '', count: 2 })).toBe(
			'/admin/usage/summary?start=2026-01-01&count=2'
		);
	});

	it('normalizes FastAPI detail errors', async () => {
		const error = await toApiError(new Response(JSON.stringify({ detail: 'invalid_admin_token' }), { status: 401 }));
		expect(error).toMatchObject({ status: 401, message: 'invalid_admin_token' });
	});

	it('normalizes gateway adapter errors', async () => {
		const error = await toApiError(
			new Response(JSON.stringify({ error: { type: 'adapter_failure', message: 'boom' } }), { status: 502 })
		);
		expect(error).toMatchObject({ status: 502, message: 'adapter_failure: boom' });
	});
});
