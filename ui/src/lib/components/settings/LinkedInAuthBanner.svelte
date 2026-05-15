<script lang="ts">
	import { onMount } from 'svelte';
	import {
		clearLinkedInAuthError,
		getLinkedInSearchStatus,
		type LinkedInSearchStatus,
	} from '$lib/api/client';

	let status = $state<LinkedInSearchStatus | null>(null);
	let clearing = $state(false);
	let error = $state<string | null>(null);

	const isPaused = $derived(status?.state === 'paused_auth_required');

	async function refresh() {
		try {
			status = await getLinkedInSearchStatus();
		} catch (err) {
			// Surface only if there's nothing showing — otherwise stay silent so
			// the banner doesn't flicker on transient API hiccups.
			if (status === null) {
				error = err instanceof Error ? err.message : 'Failed to load scheduler status';
			}
		}
	}

	async function handleClear() {
		clearing = true;
		error = null;
		try {
			await clearLinkedInAuthError();
			await refresh();
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to clear auth error';
		} finally {
			clearing = false;
		}
	}

	onMount(refresh);
</script>

{#if isPaused}
	<section
		class="border-4 border-[var(--color-error)] bg-amber-50 p-5 shadow-brutal"
		role="alert"
		aria-live="polite"
	>
		<h2 class="font-heading mb-2 text-base tracking-tight text-[var(--color-error)]">
			LinkedIn session expired
		</h2>
		<p class="mb-3 font-mono text-xs leading-relaxed text-[var(--color-foreground)]">
			Scheduled searches are paused. Refresh <code class="bg-white px-1">data/linkedin_cookies.json</code>
			on the server, then click <strong>Clear after refresh</strong> below to resume.
		</p>
		{#if status?.last_auth_error_message}
			<p class="mb-3 break-words font-mono text-xs text-[var(--color-muted-foreground)]">
				{status.last_auth_error_message}
			</p>
		{/if}
		{#if status?.last_auth_error_at}
			<p class="mb-3 font-mono text-xs text-[var(--color-muted-foreground)]">
				Last error at {new Date(status.last_auth_error_at).toLocaleString()}
			</p>
		{/if}
		{#if error}
			<p class="mb-3 font-mono text-xs text-[var(--color-error)]">{error}</p>
		{/if}
		<button
			type="button"
			onclick={handleClear}
			disabled={clearing}
			class="border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-4 py-2 font-mono text-xs uppercase tracking-wider text-[var(--color-primary-foreground)] shadow-brutal transition-all duration-200 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50"
		>
			{clearing ? 'Clearing…' : 'Clear after refresh'}
		</button>
	</section>
{/if}
