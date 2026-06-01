import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig, type ProxyOptions } from 'vite';

const backendTarget = process.env.LLM_GATEWAY_BACKEND_URL ?? 'http://127.0.0.1:18080';

function gatewayProxy(): ProxyOptions {
	return {
		target: backendTarget,
		xfwd: true
	};
}

export default defineConfig({
	plugins: [sveltekit()],
	server: {
		proxy: {
			'/admin': gatewayProxy(),
			'/auth': gatewayProxy(),
			'/health': gatewayProxy(),
			'/v1': gatewayProxy()
		}
	}
});
