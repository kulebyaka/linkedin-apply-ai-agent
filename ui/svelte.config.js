import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),

	kit: {
		adapter: adapter({
			pages: 'build',
			assets: 'build',
			// SPA mode: single index.html fallback; client-side router handles all routes.
			// Required because +layout.ts disables prerendering app-wide (ssr=false, prerender=false).
			fallback: 'index.html',
			precompress: false,
			strict: false
		})
	}
};

export default config;
