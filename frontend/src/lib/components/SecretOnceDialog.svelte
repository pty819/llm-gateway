<script lang="ts">
	let {
		secret,
		onClose
	}: {
		secret: string;
		onClose: () => void;
	} = $props();
	let copied = $state(false);

	async function copy() {
		await navigator.clipboard.writeText(secret);
		copied = true;
	}
</script>

{#if secret}
	<div class="modal-backdrop" role="presentation">
		<section class="modal" aria-label="一次性网关密钥">
			<header>
				<h2>网关密钥已签发</h2>
				<p>明文密钥只会在此刻显示一次。</p>
			</header>
			<pre class="secret">{secret}</pre>
			<footer>
				<button type="button" onclick={copy}>{copied ? '已复制' : '复制密钥'}</button>
				<button class="secondary" type="button" onclick={onClose}>关闭并隐藏</button>
			</footer>
		</section>
	</div>
{/if}
