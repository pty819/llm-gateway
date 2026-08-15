export type UpstreamFormat = 'openai' | 'openai_chat_completions' | 'anthropic' | 'hosted_vllm';

export const UPSTREAM_FORMAT_PREFIX: Record<UpstreamFormat, string> = {
	openai: 'openai',
	openai_chat_completions: 'openai/chat_completions',
	anthropic: 'anthropic',
	hosted_vllm: 'hosted_vllm'
};

export const UPSTREAM_FORMAT_LABEL: Record<UpstreamFormat, string> = {
	openai: 'OpenAI 兼容 — /v1/responses 直转原生 /responses',
	openai_chat_completions: 'OpenAI Chat Completions — 三种入口全走 /chat/completions',
	anthropic: 'Anthropic 原生 — /v1/responses 桥接 /v1/messages',
	hosted_vllm: 'vLLM 原生 — /v1/responses 直转原生 /responses'
};

export const UPSTREAM_FORMAT_SHORT_LABEL: Record<UpstreamFormat, string> = {
	openai: 'OpenAI',
	openai_chat_completions: 'ChatCompl',
	anthropic: 'Anthropic',
	hosted_vllm: 'vLLM'
};

// 正向:格式 + 上游模型名 → litellm_model
export function composeLitellmModel(format: UpstreamFormat, upstreamModelName: string): string {
	return `${UPSTREAM_FORMAT_PREFIX[format]}/${upstreamModelName}`;
}

// 反向:从现有 litellm_model 推导格式(注意 openai/chat_completions/ 必须先于 openai/ 匹配)
export function deriveUpstreamFormat(litellmModel: string): UpstreamFormat {
	const v = (litellmModel ?? '').toLowerCase();
	if (v.startsWith('openai/chat_completions/')) return 'openai_chat_completions';
	if (v.startsWith('anthropic/')) return 'anthropic';
	if (v.startsWith('hosted_vllm/')) return 'hosted_vllm';
	return 'openai'; // 默认 / openai/ 前缀
}
