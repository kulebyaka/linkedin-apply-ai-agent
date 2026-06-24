<script lang="ts">
	import { onMount } from 'svelte';
	import { getExtensionToken } from '$lib/api/auth';

	type Status = 'connecting' | 'success' | 'error' | 'no-extension';

	let status = $state<Status>('connecting');
	let error = $state<string | null>(null);

	const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';
	// The extension ID is assigned by Chrome at install time; the user pastes it
	// into VITE_EXTENSION_ID (or appends ?ext=<id>) so the page can target it.
	const ENV_EXT_ID = import.meta.env.VITE_EXTENSION_ID ?? '';

	// Derive the WS endpoint from the API base (http -> ws, https -> wss).
	function wsUrl(): string {
		const base = API_BASE.replace(/^http/, 'ws');
		return `${base}/ws/extension`;
	}

	function extensionId(): string {
		const fromQuery = new URLSearchParams(window.location.search).get('ext');
		return fromQuery || ENV_EXT_ID;
	}

	onMount(async () => {
		// The `chrome` runtime is only injected on the page when the extension is
		// installed and lists this origin under `externally_connectable`.
		const runtime = (window as unknown as { chrome?: { runtime?: any } }).chrome?.runtime;
		const extId = extensionId();

		if (!runtime || typeof runtime.sendMessage !== 'function' || !extId) {
			status = 'no-extension';
			return;
		}

		try {
			const token = await getExtensionToken();
			runtime.sendMessage(
				extId,
				{
					type: 'SET_TOKEN',
					token,
					wsUrl: wsUrl(),
					appOrigin: window.location.origin
				},
				(response: { ok?: boolean; error?: string } | undefined) => {
					if (runtime.lastError) {
						status = 'error';
						error = runtime.lastError.message ?? 'Could not reach the extension';
						return;
					}
					if (response && response.ok) {
						status = 'success';
					} else {
						status = 'error';
						error = response?.error ?? 'The extension rejected the token';
					}
				}
			);
		} catch (err) {
			status = 'error';
			error = err instanceof Error ? err.message : 'Failed to obtain an extension token';
		}
	});
</script>

<svelte:head>
	<title>Connect Extension - Job Application Agent</title>
</svelte:head>

<div class="grain-texture min-h-screen bg-[var(--color-background)] px-4 py-16 sm:px-6 lg:px-8">
	<div class="mx-auto max-w-md">
		<div class="border-4 border-[var(--color-foreground)] bg-white p-8 shadow-brutal">
			{#if status === 'connecting'}
				<h2 class="font-heading mb-2 text-xl text-[var(--color-foreground)]">
					Connecting the extension…
				</h2>
				<p class="font-body text-sm text-[var(--color-muted-foreground)]">
					Handing your session token to the browser extension.
				</p>
			{:else if status === 'success'}
				<h2 class="font-heading mb-2 text-xl text-[var(--color-success)]">
					Extension connected
				</h2>
				<p class="font-body text-sm text-[var(--color-muted-foreground)]">
					You can close this tab. The extension will apply on your behalf when you approve a
					job.
				</p>
			{:else if status === 'no-extension'}
				<h2 class="font-heading mb-2 text-xl text-[var(--color-foreground)]">
					Extension not detected
				</h2>
				<p class="font-body mb-4 text-sm text-[var(--color-muted-foreground)]">
					Install the LinkedIn Apply Bridge extension, then reopen this page. If it's already
					installed, append your extension ID as <code>?ext=&lt;id&gt;</code> or set
					<code>VITE_EXTENSION_ID</code>.
				</p>
			{:else}
				<h2 class="font-heading mb-2 text-xl text-[var(--color-error)]">
					Connection failed
				</h2>
				<p class="font-body text-sm text-[var(--color-muted-foreground)]">
					{error ?? 'Something went wrong handing the token to the extension.'}
				</p>
			{/if}
		</div>
	</div>
</div>
