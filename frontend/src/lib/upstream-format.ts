// All upstreams now speak OpenAI Chat Completions or OpenAI Responses — both
// use a bare model name forwarded as-is to the upstream. The "format" concept
// is kept as a single value for UI/back-compat with existing data; new model
// aliases store the bare upstream model name in litellm_model (column name
// unchanged to avoid a migration).

export type UpstreamFormat = 'openai';

export const UPSTREAM_FORMAT_PREFIX: Record<UpstreamFormat, string> = {
	openai: 'openai'
};

export const UPSTREAM_FORMAT_LABEL: Record<UpstreamFormat, string> = {
	openai: 'OpenAI 兼容 (chat/completions / responses)'
};

export const UPSTREAM_FORMAT_SHORT_LABEL: Record<UpstreamFormat, string> = {
	openai: 'OpenAI'
};

// Forward: bare upstream model name → litellm_model (no provider prefix).
export function composeLitellmModel(
	_format: UpstreamFormat,
	upstreamModelName: string
): string {
	return upstreamModelName;
}

// Reverse: legacy litellm_model values may carry a provider prefix (openai/,
// anthropic/, hosted_vllm/) — strip it so the UI shows the bare model name.
export function deriveUpstreamFormat(_litellmModel: string): UpstreamFormat {
	return 'openai';
}

// Strip any legacy provider prefix (openai/, openai/chat_completions/,
// anthropic/, hosted_vllm/) from a stored litellm_model for display.
export function bareModelName(litellmModel: string): string {
	const v = litellmModel ?? '';
	if (v.startsWith('openai/chat_completions/')) return v.slice('openai/chat_completions/'.length);
	if (v.startsWith('openai/')) return v.slice('openai/'.length);
	if (v.startsWith('anthropic/')) return v.slice('anthropic/'.length);
	if (v.startsWith('hosted_vllm/')) return v.slice('hosted_vllm/'.length);
	return v;
}
