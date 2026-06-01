import { describe, expect, it } from 'vitest';
import viteConfig from '../../../vite.config';

describe('vite proxy config', () => {
	it('forwards source IP metadata to the backend gateway', () => {
		const proxy = viteConfig.server?.proxy;
		expect(proxy).toBeTruthy();
		if (!proxy || Array.isArray(proxy)) {
			throw new Error('Expected object proxy config');
		}

		for (const path of ['/admin', '/auth', '/health', '/v1']) {
			const entry = proxy[path];
			expect(entry).toMatchObject({
				xfwd: true,
				target: 'http://127.0.0.1:18080'
			});
		}
	});
});
