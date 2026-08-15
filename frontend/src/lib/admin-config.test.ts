import { describe, expect, it } from 'vitest';
import { datetimeLocalToUtcIso, parseServerUtcIso } from './admin-config';

describe('datetimeLocalToUtcIso', () => {
	it('returns empty string for empty input', () => {
		expect(datetimeLocalToUtcIso('')).toBe('');
	});

	it('emits an offset-aware UTC ISO string equal to the local interpretation', () => {
		const value = '2026-08-15T14:00';
		const out = datetimeLocalToUtcIso(value);
		// Contract: output must be UTC-marked ISO of the same instant the
		// browser sees for the naive datetime-local value.
		expect(out).toMatch(/Z$/);
		expect(new Date(out).getTime()).toBe(new Date(value).getTime());
	});

	it('returns empty string for garbage input', () => {
		expect(datetimeLocalToUtcIso('not-a-date')).toBe('');
	});
});

describe('parseServerUtcIso', () => {
	it('marks naive server timestamps as UTC', () => {
		const naive = '2026-08-15T07:00:00';
		expect(parseServerUtcIso(naive).getTime()).toBe(Date.parse(`${naive}Z`));
	});

	it('passes offset-aware values through unchanged', () => {
		const aware = '2026-08-15T07:00:00+00:00';
		expect(parseServerUtcIso(aware).getTime()).toBe(Date.parse(aware));
	});

	it('passes Z-suffixed values through unchanged', () => {
		const z = '2026-08-15T07:00:00Z';
		expect(parseServerUtcIso(z).getTime()).toBe(Date.parse(z));
	});
});
