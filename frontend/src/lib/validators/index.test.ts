import { describe, expect, it } from 'vitest';
import { maskSecrets, parseJsonObject, validateCidrList, validateHttpUrl, validatePort } from './index';

describe('validators', () => {
	it('accepts valid CIDR lists', () => {
		expect(validateCidrList('127.0.0.1/32\n10.0.0.0/8').ok).toBe(true);
	});

	it('rejects invalid CIDR values', () => {
		expect(validateCidrList('not-a-cidr').ok).toBe(false);
	});

	it('validates operational URLs and ports', () => {
		expect(validateHttpUrl('https://example.com/v1').ok).toBe(true);
		expect(validateHttpUrl('ftp://example.com').ok).toBe(false);
		expect(validatePort(18001).ok).toBe(true);
		expect(validatePort(70000).ok).toBe(false);
	});

	it('parses only JSON objects', () => {
		expect(parseJsonObject('{"x":1}')).toEqual({ x: 1 });
		expect(() => parseJsonObject('[]')).toThrow('must be a JSON object');
	});

	it('masks secret-looking keys recursively', () => {
		expect(maskSecrets({ api_key_value: 'abc', nested: { token: 'def', name: 'ok' } })).toEqual({
			api_key_value: '••••••••',
			nested: { token: '••••••••', name: 'ok' }
		});
	});
});
