import { describe, expect, it } from 'vitest';
import { fmtNumber, fmtPercent } from './format';

describe('fmtNumber', () => {
	it('renders small integers verbatim', () => {
		expect(fmtNumber(0)).toBe('0');
		expect(fmtNumber(9999)).toBe('9999');
		expect(fmtNumber(-12)).toBe('-12');
	});

	it('adds thousands separators from 10,000', () => {
		expect(fmtNumber(10_000)).toBe('10,000');
		expect(fmtNumber(12_345)).toBe('12,345');
		expect(fmtNumber(99_999)).toBe('99,999');
	});

	it('compacts from 100,000 with one decimal and trailing-zero trim', () => {
		expect(fmtNumber(100_000)).toBe('100K');
		expect(fmtNumber(123_456)).toBe('123.5K');
		expect(fmtNumber(999_999)).toBe('1000K');
	});

	it('compacts millions and billions', () => {
		expect(fmtNumber(1_000_000)).toBe('1M');
		expect(fmtNumber(123_456_789)).toBe('123.5M');
		expect(fmtNumber(2_500_000_000)).toBe('2.5B');
	});

	it('handles negatives and non-finite values', () => {
		expect(fmtNumber(-123_456)).toBe('-123.5K');
		expect(fmtNumber(null)).toBe('—');
		expect(fmtNumber(undefined)).toBe('—');
		expect(fmtNumber(Number.NaN)).toBe('—');
		expect(fmtNumber(Number.POSITIVE_INFINITY)).toBe('—');
	});

	it('rounds small non-integers to one decimal', () => {
		expect(fmtNumber(12.34)).toBe('12.3');
	});
});

describe('fmtPercent', () => {
	it('formats ratios with one decimal', () => {
		expect(fmtPercent(1)).toBe('100.0%');
		expect(fmtPercent(0.9876)).toBe('98.8%');
		expect(fmtPercent(0)).toBe('0.0%');
	});

	it('returns dash for empty values', () => {
		expect(fmtPercent(null)).toBe('—');
		expect(fmtPercent(undefined)).toBe('—');
	});
});
