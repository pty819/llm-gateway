<script lang="ts">
	import type { AdminApiClient } from '$lib/api/client';
	import type { McpTeamGrantSummary } from '$lib/api/types';

	let {
		client,
		slug,
		grants,
		teams,
		onChanged
	}: {
		client: AdminApiClient;
		slug: string;
		grants: McpTeamGrantSummary[];
		teams: { id: string; name: string }[];
		onChanged?: () => void;
	} = $props();

	let selectedTeamId = $state('');
	let error = $state('');
	let busy = $state(false);

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
			await client.grantMcp(slug, selectedTeamId);
			selectedTeamId = '';
			onChanged?.();
		} catch (err) {
			error = err instanceof Error ? err.message : '授权失败。';
		} finally {
			busy = false;
		}
	}

	async function revoke(grant: McpTeamGrantSummary) {
		if (!window.confirm(`确认撤销 ${teamName(grant.team_id)} 对该 MCP 的授权？`)) return;
		error = '';
		busy = true;
		try {
			await client.revokeMcpGrant(slug, grant.id);
			onChanged?.();
		} catch (err) {
			error = err instanceof Error ? err.message : '撤销失败。';
		} finally {
			busy = false;
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
							<button class="danger" type="button" onclick={() => revoke(grant)} disabled={busy}>撤销</button>
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
