import { describe, expect, it } from 'vitest';
import {
	UPSTREAM_FORMAT_LABEL,
	UPSTREAM_FORMAT_PREFIX,
	UPSTREAM_FORMAT_SHORT_LABEL,
	bareModelName,
	composeLitellmModel,
	deriveUpstreamFormat
} from './upstream-format';

describe('upstream-format', () => {
	describe('composeLitellmModel', () => {
		it('returns the bare upstream model name (no provider prefix)', () => {
			expect(composeLitellmModel('openai', 'gpt-4o')).toBe('gpt-4o');
		});

		it('preserves a slash-bearing model name verbatim', () => {
			expect(composeLitellmModel('openai', 'org/gpt-4o')).toBe('org/gpt-4o');
		});
	});

	describe('deriveUpstreamFormat', () => {
		it('always returns openai (only one format remains)', () => {
			expect(deriveUpstreamFormat('openai/gpt-4o')).toBe('openai');
			expect(deriveUpstreamFormat('openai/chat_completions/gpt-4o')).toBe('openai');
			expect(deriveUpstreamFormat('anthropic/claude-3-5-sonnet')).toBe('openai');
			expect(deriveUpstreamFormat('hosted_vllm/qwen2-7b')).toBe('openai');
		});

		it('defaults to openai for empty input', () => {
			expect(deriveUpstreamFormat('')).toBe('openai');
		});
	});

	describe('bareModelName', () => {
		it('strips the openai/ prefix', () => {
			expect(bareModelName('openai/gpt-4o')).toBe('gpt-4o');
		});

		it('strips the openai/chat_completions/ prefix', () => {
			expect(bareModelName('openai/chat_completions/gpt-4o')).toBe('gpt-4o');
		});

		it('strips the anthropic/ prefix', () => {
			expect(bareModelName('anthropic/claude-3-5-sonnet')).toBe('claude-3-5-sonnet');
		});

		it('strips the hosted_vllm/ prefix', () => {
			expect(bareModelName('hosted_vllm/qwen2-7b')).toBe('qwen2-7b');
		});

		it('returns bare names unchanged', () => {
			expect(bareModelName('gpt-4o')).toBe('gpt-4o');
		});

		it('preserves a slash-bearing model name under a known prefix', () => {
			expect(bareModelName('openai/org/gpt-4o')).toBe('org/gpt-4o');
		});

		it('returns unknown prefixes unchanged', () => {
			expect(bareModelName('vertex/gemini-1.5-pro')).toBe('vertex/gemini-1.5-pro');
		});
	});

	describe('label tables', () => {
		it('keeps a label for every format in both short and long tables', () => {
			for (const key of Object.keys(UPSTREAM_FORMAT_PREFIX) as Array<keyof typeof UPSTREAM_FORMAT_PREFIX>) {
				expect(typeof UPSTREAM_FORMAT_LABEL[key]).toBe('string');
				expect(UPSTREAM_FORMAT_LABEL[key].length).toBeGreaterThan(0);
				expect(typeof UPSTREAM_FORMAT_SHORT_LABEL[key]).toBe('string');
				expect(UPSTREAM_FORMAT_SHORT_LABEL[key].length).toBeGreaterThan(0);
			}
		});
	});
});
