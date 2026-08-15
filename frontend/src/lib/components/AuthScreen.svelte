<script lang="ts">
	import type { ReadyStatus } from '$lib/api/types';

	/** 登录页(设计稿 L7):去掉空侧栏,左侧品牌面板 + 右侧登录/注册 segmented 表单卡。
	 * 错误提示统一由父组件的 run()/fail() 以 Toast 呈现,此处不重复弹。 */
	let {
		ready,
		loginForm = $bindable(),
		registerForm = $bindable(),
		rememberSession = $bindable(),
		pageError,
		loading,
		onLogin,
		onRegister,
		onRefreshReady
	}: {
		ready: ReadyStatus | null;
		loginForm: { username: string; password: string };
		registerForm: { username: string; full_name: string; password: string };
		rememberSession: boolean;
		pageError: string;
		loading: boolean;
		onLogin: (fromStorage?: boolean) => void | Promise<void>;
		onRegister: () => void | Promise<void>;
		onRefreshReady: () => void | Promise<void>;
	} = $props();

	let mode = $state<'login' | 'register'>('login');
</script>

<div class="auth-shell">
	<aside class="auth-brand">
		<div class="auth-brand-logo">
			<span class="brand-logo" style="width:40px; height:40px;">
				<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#e7ecf5" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
					<path d="M8 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h3" />
					<path d="M16 4h3a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-3" />
					<path d="m9 12 3-3" />
					<path d="m12 9 3 3-3 3" />
				</svg>
			</span>
			<strong style="font-size: 18px; color: var(--rail-ink);">LLM Gateway</strong>
		</div>
		<div style="display:grid; gap: 16px;">
			<h1>模型流量调度台</h1>
			<p>统一接入 Codex / Claude Code / OpenAI 客户端，路由到内部推理端点。配额、限流、审计，一站式管理。</p>
		</div>
		<div class="auth-brand-foot">
			{#if ready}
				<span style="display:flex; align-items:center; gap:8px;">
					<span class="status-dot" class:ok={ready.checks.postgres}>Postgres</span>
					<span class="status-dot" class:ok={ready.checks.redis}>Redis</span>
					<span class={ready.ok ? 'badge success' : 'badge danger'}>{ready.ok ? '就绪' : '未就绪'}</span>
					<button class="ghost" style="color: var(--rail-muted); height:24px; font-size:11px;" type="button" onclick={onRefreshReady}>刷新</button>
				</span>
			{:else}
				<span>正在探测网关就绪状态…</span>
			{/if}
		</div>
	</aside>
	<main class="auth-main">
		<section class="auth-card">
			<div class="segmented">
				<button type="button" class:active={mode === 'login'} onclick={() => (mode = 'login')}>登录</button>
				<button type="button" class:active={mode === 'register'} onclick={() => (mode = 'register')}>注册</button>
			</div>
			{#if mode === 'login'}
				<h2>欢迎回来</h2>
				<p>使用你的网关账号进入控制台。</p>
				<label>
					用户名
					<input bind:value={loginForm.username} autocomplete="username" placeholder="工号,例如 l00014624" />
				</label>
				<label>
					密码
					<input type="password" bind:value={loginForm.password} autocomplete="current-password" onkeydown={(event) => event.key === 'Enter' && onLogin()} />
				</label>
				<label class="check-label">
					<input type="checkbox" bind:checked={rememberSession} />
					在这台设备上记住登录
				</label>
				<button type="button" onclick={() => onLogin()} disabled={loading}>
					{#if loading}<span class="spinner"></span>{/if}
					登录
				</button>
			{:else}
				<h2>创建账号</h2>
				<p>新用户会自动加入 <code>guest</code> 权限组，并立即获得一个网关密钥。</p>
				<label>工号<input bind:value={registerForm.username} autocomplete="username" placeholder="l00014624" /></label>
				<label>真实姓名<input bind:value={registerForm.full_name} autocomplete="name" /></label>
				<label>密码<input type="password" bind:value={registerForm.password} autocomplete="new-password" /></label>
				<button type="button" onclick={onRegister} disabled={loading}>
					{#if loading}<span class="spinner"></span>{/if}
					创建账号
				</button>
			{/if}
		</section>
	</main>
</div>
