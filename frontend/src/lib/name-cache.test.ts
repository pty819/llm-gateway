import { describe, expect, it } from 'vitest';
import {
	emptyNameCache,
	mergeEntitlementRows,
	mergeKeyRefs,
	mergeModelRefs,
	mergeOwnerRefs,
	mergeProjectMembershipRows,
	mergeRatePolicyRows,
	mergeSubjectRefs,
	mergeTeamMembershipRows,
	mergeUsageRows
} from '$lib/name-cache';

describe('name cache merges', () => {
	it('merges embedded subject refs with login fallback to existing entry', () => {
		let cache = mergeSubjectRefs(emptyNameCache(), [
			{ subject_id: 's1', subject_name: '张三', subject_login_username: 'l00000001' }
		]);
		cache = mergeSubjectRefs(cache, [{ subject_id: 's1', subject_name: '张三' }]);
		expect(cache.subjects['s1']).toEqual({ name: '张三', login_username: 'l00000001' });
	});

	it('ignores entries without id or name', () => {
		const cache = mergeSubjectRefs(emptyNameCache(), [
			{ subject_id: null, subject_name: 'x' },
			{ subject_id: 's2', subject_name: null }
		]);
		expect(cache.subjects).toEqual({});
	});

	it('merges project owner names and the project itself', () => {
		const cache = mergeOwnerRefs(emptyNameCache(), [
			{
				id: 'p1',
				name: 'proj',
				owner_subject_id: 's1',
				owner_name: '李四',
				owner_login_username: null
			} as never
		]);
		expect(cache.projects['p1']).toBe('proj');
		expect(cache.subjects['s1']).toEqual({ name: '李四', login_username: null });
	});

	it('merges memberships into subject and project maps', () => {
		const cache = mergeProjectMembershipRows(emptyNameCache(), [
			{
				project_id: 'p1',
				project_name: 'proj',
				subject_id: 's1',
				subject_name: '王五',
				subject_login_username: 'l00000003'
			} as never
		]);
		expect(cache.subjects['s1']?.name).toBe('王五');
		expect(cache.projects['p1']).toBe('proj');
	});

	it('merges key rows with subject/project refs and key identity', () => {
		const cache = mergeKeyRefs(emptyNameCache(), [
			{
				id: 'k1',
				name: 'prod key',
				key_prefix: 'gw-abc',
				subject_id: 's1',
				subject_name: '赵六',
				subject_login_username: 'l00000004',
				project_id: 'p1',
				project_name: 'proj2'
			}
		]);
		expect(cache.keys['k1']).toEqual({ name: 'prod key', key_prefix: 'gw-abc' });
		expect(cache.subjects['s1']?.name).toBe('赵六');
		expect(cache.projects['p1']).toBe('proj2');
	});

	it('merges model alias refs', () => {
		const cache = mergeModelRefs(emptyNameCache(), [{ model_alias_id: 'm1', model_alias: 'dev-model' }]);
		expect(cache.models['m1']).toBe('dev-model');
	});

	it('merges team membership rows into both maps', () => {
		const cache = mergeTeamMembershipRows(emptyNameCache(), [
			{ team_id: 't1', team_name: 'dev', subject_id: 's1', subject_name: '孙七' } as never
		]);
		expect(cache.teams['t1']).toBe('dev');
		expect(cache.subjects['s1']?.name).toBe('孙七');
	});

	it('merges entitlement rows including key names', () => {
		const cache = mergeEntitlementRows(emptyNameCache(), [
			{
				model_alias_id: 'm1',
				model_alias: 'dev-model',
				gateway_key_id: 'k1',
				key_name: 'the key',
				project_id: 'p1',
				project_name: 'proj'
			} as never
		]);
		expect(cache.models['m1']).toBe('dev-model');
		expect(cache.keys['k1']?.name).toBe('the key');
		expect(cache.projects['p1']).toBe('proj');
	});

	it('routes rate policy scope names into the right map', () => {
		const cache = mergeRatePolicyRows(emptyNameCache(), [
			{ scope: 'subject', scope_id: 's1', scope_name: '周八' },
			{ scope: 'project', scope_id: 'p1', scope_name: 'proj' },
			{ scope: 'key', scope_id: 'k1', scope_name: 'k' }
		] as never);
		expect(cache.subjects['s1']?.name).toBe('周八');
		expect(cache.projects['p1']).toBe('proj');
		expect(cache.keys['k1']).toBeDefined();
	});

	it('merges usage rows with embedded names', () => {
		const cache = mergeUsageRows(emptyNameCache(), [
			{ subject_id: 's1', subject_name: '吴九', project_id: 'p1', project_name: 'proj' } as never
		]);
		expect(cache.subjects['s1']?.name).toBe('吴九');
		expect(cache.projects['p1']).toBe('proj');
	});

	it('keeps the original cache untouched (immutability)', () => {
		const base = emptyNameCache();
		mergeSubjectRefs(base, [{ subject_id: 's1', subject_name: 'x' }]);
		expect(base.subjects).toEqual({});
	});
});
