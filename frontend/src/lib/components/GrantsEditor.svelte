<script lang="ts">
	import type { SkillTeamGrantSummary, McpTeamGrantSummary } from '$lib/api/types';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';

	/**
	 * Unified grant editor for Skill and MCP artifacts. The two former editors
	 * (`ArtifactGrantsEditor.svelte`, `McpGrantsEditor.svelte`) were 107-line
	 * clones differing only in their grant type and the two client calls; this
	 * component takes the grant list plus `onGrant` / `onRevoke` callbacks so
	 * each caller binds its own API.
	 *
	 * `artifactLabel` only customises the confirmation message wording.
	 */

	type Grant = SkillTeamGrantSummary | McpTeamGrantSummary;

	let {
		grants,
		teams,
		onGrant,
		onRevoke,
		artifactLabel = '授权',
		onChanged
	}: {
		grants: Grant[];
		teams: { id: string; name: string }[];
		onGrant: (teamId: string) => Promise<unknown>;
		onRevoke: (grantId: string) => Promise<unknown>;
		artifactLabel?: string;
		onChanged?: () => void;
	} = $props();

	let selectedTeamId = $state('');
	let error = $state('');
	let busy = $state(false);

	/** Pending revoke, shown in the confirm dialog. */
	let pendingRevoke = $state<Grant | null>(null);
	/** Drives the ConfirmDialog; null = closed. */
	let confirmMessage = $state<string | null>(null);

	function teamName(teamId: string): string {
		return teams.find((team) => team.id === teamId)?.name ?? teamId;
	}

	// 已经存在授权记录(任意状态)的权限组,默认不在下拉里重复出现。
	let candidateTeams = $derived(teams.filter((team) => !grants.some((grant) => grant.team_id === team.id)));

	async function authorize() {
		if (!selectedTeamId) {
			error = '请选择要授权的权限组。';
			return;
		}
		error = '';
		busy = true;
		try {
			await onGrant(selectedTeamId);
			selectedTeamId = '';
			onChanged?.();
		} catch (err) {
			error = err instanceof Error ? err.message : '授权失败。';
		} finally {
			busy = false;
		}
	}

	function askRevoke(grant: Grant) {
		pendingRevoke = grant;
		confirmMessage = `确认撤销 ${teamName(grant.team_id)} 对该${artifactLabel}的授权？`;
	}

	async function doRevoke() {
		const grant = pendingRevoke;
		if (!grant) return;
		error = '';
		busy = true;
		try {
			await onRevoke(grant.id);
			onChanged?.();
		} catch (err) {
			error = err instanceof Error ? err.message : '撤销失败。';
		} finally {
			busy = false;
			pendingRevoke = null;
		}
	}
</script>

<div class="actions">
	<label style="display:grid; width:auto; flex:1 1 240px;">
		授权权限组
		<select bind:value={selectedTeamId} disabled={busy}>
			<option value="">选择权限组</option>
			{#each candidateTeams as team (team.id)}
				<option value={team.id}>{team.name}</option>
			{/each}
		</select>
	</label>
	<button type="button" onclick={authorize} disabled={busy}>{busy ? '处理中' : '授权'}</button>
</div>
{#if candidateTeams.length === 0 && teams.length > 0}
	<p class="muted">所有权限组均已授权。</p>
{:else if teams.length === 0}
	<p class="muted">暂无可授权的权限组。</p>
{/if}
{#if error}<p class="error">{error}</p>{/if}

<div class="table-wrap">
	<table>
		<thead>
			<tr><th>权限组</th><th>状态</th><th>操作</th></tr>
		</thead>
		<tbody>
			{#each grants as grant (grant.id)}
				<tr>
					<td>{teamName(grant.team_id)}</td>
					<td>
						<span class="badge {grant.state === 'active' ? 'success' : ''}">{grant.state === 'active' ? '已启用' : grant.state}</span>
					</td>
						<td>
							{#if grant.state === 'active'}
								<button class="danger" type="button" onclick={() => askRevoke(grant)} disabled={busy}>撤销</button>
							{:else}
								<span class="muted">已撤销</span>
							{/if}
						</td>
				</tr>
			{:else}
				<tr><td colspan="3" class="empty">尚未授权任何权限组。</td></tr>
			{/each}
		</tbody>
	</table>
</div>

<ConfirmDialog
	bind:message={confirmMessage}
	title="撤销授权"
	confirmLabel="撤销"
	{busy}
	onConfirm={doRevoke}
/>
