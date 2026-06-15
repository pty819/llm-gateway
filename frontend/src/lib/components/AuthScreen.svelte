<script lang="ts">
	import type { ReadyStatus } from '$lib/api/types';
	import StateBadge from '$lib/components/StateBadge.svelte';

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
</script>

<div class="app">
	<aside class="sidebar">
		<div class="brand">
			<strong>LLM Gateway</strong>
			<span>账号访问</span>
		</div>
	</aside>
	<main class="main">
		<section class="content">
			<div class="split" style="align-items: start;">
			<div class="panel">
				<h1>登录</h1>
				<p>使用你的网关账号进入控制台。</p>
				{#if ready}
					<div class="actions">
						<StateBadge value={ready.ok ? 'ready' : 'not_ready'} tone={ready.ok ? 'success' : 'danger'} />
						<span class="muted">Postgres {ready.checks.postgres ? '正常' : '异常'} · Redis {ready.checks.redis ? '正常' : '异常'}</span>
					</div>
				{/if}
				<label>
					用户名
					<input bind:value={loginForm.username} autocomplete="username" />
				</label>
				<label>
					密码
					<input type="password" bind:value={loginForm.password} autocomplete="current-password" onkeydown={(event) => event.key === 'Enter' && onLogin()} />
				</label>
				<label style="display: flex; grid-template-columns: auto 1fr; align-items: center;">
					<input type="checkbox" bind:checked={rememberSession} style="width: auto;" />
					在这台设备上记住登录
				</label>
				{#if pageError}<div class="error">{pageError}</div>{/if}
				<div class="actions">
					<button type="button" onclick={() => onLogin()} disabled={loading}>{loading ? '登录中' : '登录'}</button>
					<button class="secondary" type="button" onclick={onRefreshReady}>刷新就绪状态</button>
				</div>
			</div>
			<div class="panel">
				<h1>注册</h1>
				<p>新用户会自动加入 <code>guest</code> 权限组，并立即获得一个网关密钥。</p>
				<label>工号<input bind:value={registerForm.username} autocomplete="username" placeholder="l00014624" /></label>
				<label>真实姓名<input bind:value={registerForm.full_name} autocomplete="name" /></label>
				<label>密码<input type="password" bind:value={registerForm.password} autocomplete="new-password" /></label>
				<div class="actions">
					<button type="button" onclick={onRegister} disabled={loading}>创建账号</button>
				</div>
			</div>
			</div>
		</section>
	</main>
</div>
