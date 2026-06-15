import { describe, expect, it } from 'vitest';
import {
	UPSTREAM_FORMAT_LABEL,
	UPSTREAM_FORMAT_PREFIX,
	UPSTREAM_FORMAT_SHORT_LABEL,
	composeLitellmModel,
	deriveUpstreamFormat
} from './upstream-format';

describe('upstream-format', () => {
	describe('composeLitellmModel', () => {
		it('joins every format prefix with the upstream model name', () => {
			expect(composeLitellmModel('openai', 'gpt-4o')).toBe('openai/gpt-4o');
			expect(composeLitellmModel('openai_chat_completions', 'gpt-4o')).toBe(
				'openai/chat_completions/gpt-4o'
			);
			expect(composeLitellmModel('anthropic', 'claude-3-5-sonnet')).toBe(
				'anthropic/claude-3-5-sonnet'
			);
			expect(composeLitellmModel('hosted_vllm', 'qwen2-7b')).toBe('hosted_vllm/qwen2-7b');
		});

		it('preserves a slash-bearing model name verbatim', () => {
			expect(composeLitellmModel('openai', 'org/gpt-4o')).toBe('openai/org/gpt-4o');
		});
	});

	describe('deriveUpstreamFormat', () => {
		it('recovers each format from a composed litellm_model', () => {
			expect(deriveUpstreamFormat('openai/gpt-4o')).toBe('openai');
			expect(deriveUpstreamFormat('openai/chat_completions/gpt-4o')).toBe('openai_chat_completions');
			expect(deriveUpstreamFormat('anthropic/claude-3-5-sonnet')).toBe('anthropic');
			expect(deriveUpstreamFormat('hosted_vllm/qwen2-7b')).toBe('hosted_vllm');
		});

		it('prefers chat_completions over the bare openai prefix', () => {
			// openai/ would also match the first 7 chars; chat_completions must win
			expect(deriveUpstreamFormat('openai/chat_completions/something')).toBe('openai_chat_completions');
		});

		it('is case-insensitive on the prefix', () => {
			expect(deriveUpstreamFormat('Anthropic/claude-3')).toBe('anthropic');
			expect(deriveUpstreamFormat('HOSTED_VLLM/x')).toBe('hosted_vllm');
		});

		it('defaults to openai for empty input', () => {
			expect(deriveUpstreamFormat('')).toBe('openai');
		});

		it('defaults to openai for unknown prefixes', () => {
			expect(deriveUpstreamFormat('vertex/gemini-1.5-pro')).toBe('openai');
			expect(deriveUpstreamFormat('cohere/command-r')).toBe('openai');
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
