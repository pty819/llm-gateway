<script lang="ts">
	/**
	 * Shared mine/browse tab bar for the Skill and MCP market sections.
	 * Previously duplicated (the tab markup plus its style block) in both
	 * SkillMarketSection and McpMarketSection. Renders the tabs; sections
	 * keep their own row-level styles locally.
	 */
	let {
		tab,
		switchTab
	}: {
		tab: 'mine' | 'browse';
		switchTab: (next: 'mine' | 'browse') => void;
	} = $props();
</script>

<div class="tabs">
	<button type="button" class:active={tab === 'mine'} onclick={() => switchTab('mine')}>我发布的</button>
	<button type="button" class:active={tab === 'browse'} onclick={() => switchTab('browse')}>市场浏览</button>
</div>

<style>
	.tabs {
		display: flex;
		gap: 0.5rem;
		margin-bottom: 1rem;
	}

	.tabs button {
		padding: 0.45rem 0.9rem;
		border: 1px solid var(--border, #d8dce2);
		background: #fff;
		border-radius: 6px;
		cursor: pointer;
	}

	.tabs button.active {
		background: var(--accent-bg, #e7f0fb);
		border-color: var(--accent, #2b6cb0);
		font-weight: 600;
	}
</style>
