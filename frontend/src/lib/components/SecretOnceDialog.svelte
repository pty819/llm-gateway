<script lang="ts">
	let {
		secret,
		onClose
	}: {
		secret: string;
		onClose: () => void;
	} = $props();
	let copied = $state(false);
	let copyError = $state('');

	async function copy() {
		copyError = '';
		try {
			if (navigator.clipboard?.writeText) {
				await navigator.clipboard.writeText(secret);
			} else {
				fallbackCopy(secret);
			}
			copied = true;
		} catch {
			try {
				fallbackCopy(secret);
				copied = true;
			} catch {
				copyError = '复制失败，请手动选中密钥复制。';
			}
		}
	}

	function fallbackCopy(value: string) {
		const textarea = document.createElement('textarea');
		textarea.value = value;
		textarea.setAttribute('readonly', 'true');
		textarea.style.position = 'fixed';
		textarea.style.left = '-9999px';
		document.body.appendChild(textarea);
		textarea.select();
		const ok = document.execCommand('copy');
		document.body.removeChild(textarea);
		if (!ok) throw new Error('copy_failed');
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
			{#if copyError}<p class="error">{copyError}</p>{/if}
		</section>
	</div>
{/if}
