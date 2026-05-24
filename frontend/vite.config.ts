import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [sveltekit()],
	server: {
		proxy: {
			'/admin': 'http://127.0.0.1:8000',
			'/health': 'http://127.0.0.1:8000',
			'/v1': 'http://127.0.0.1:8000'
		}
	}
});
