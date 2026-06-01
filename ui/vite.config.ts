import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import { execSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

function resolveVersion(): string {
	// Prefer an explicit build-time env (set by CI from the git release tag)
	if (process.env.VITE_APP_VERSION) return process.env.VITE_APP_VERSION;
	// Fall back to the closest git tag / describe output for local builds
	try {
		return execSync('git describe --tags --always --dirty', {
			stdio: ['ignore', 'pipe', 'ignore'],
		})
			.toString()
			.trim();
	} catch {
		// Last resort: package.json version
		try {
			return JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf-8')).version;
		} catch {
			return 'dev';
		}
	}
}

export default defineConfig({
	plugins: [sveltekit()],
	envDir: '..',
	define: {
		__APP_VERSION__: JSON.stringify(resolveVersion()),
	},
});
