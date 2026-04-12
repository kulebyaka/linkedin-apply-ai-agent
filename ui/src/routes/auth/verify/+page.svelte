<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { verifyToken } from '$lib/api/auth';
	import { auth } from '$lib/stores/auth.svelte';

	let status = $state<'verifying' | 'success' | 'error'>('verifying');
	let error = $state<string | null>(null);

	onMount(async () => {
		const token = $page.url.searchParams.get('token');

		if (!token) {
			status = 'error';
			error = 'No verification token provided';
			return;
		}

		try {
			const result = await verifyToken(token);
			auth.setUser(result.user);
			status = 'success';
			// Redirect to home after brief success display
			setTimeout(() => goto('/'), 1500);
		} catch (err) {
			status = 'error';
			error = err instanceof Error ? err.message : 'Verification failed';
		}
	});
</script>

<svelte:head>
	<title>Verifying - Job Application Agent</title>
</svelte:head>

<div class="grain-texture min-h-screen bg-[var(--color-background)] px-4 py-16 sm:px-6 lg:px-8">
	<div class="mx-auto max-w-md">
		<div class="border-4 border-[var(--color-foreground)] bg-white p-8 shadow-brutal">
			{#if status === 'verifying'}
				<div class="text-center">
					<div
						class="mx-auto mb-6 inline-flex h-16 w-16 items-center justify-center border-4 border-[var(--color-primary)] animate-pulse"
					>
						<svg
							class="h-8 w-8 text-[var(--color-primary)]"
							fill="none"
							stroke="currentColor"
							viewBox="0 0 24 24"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
							></path>
						</svg>
					</div>
					<h2 class="font-heading mb-2 text-xl text-[var(--color-foreground)]">
						Verifying...
					</h2>
					<p class="font-body text-sm text-[var(--color-muted-foreground)]">
						Validating your magic link
					</p>
				</div>
			{:else if status === 'success'}
				<div class="text-center">
					<div
						class="mx-auto mb-6 inline-flex h-16 w-16 items-center justify-center border-4 border-[var(--color-success)]"
					>
						<svg
							class="h-8 w-8 text-[var(--color-success)]"
							fill="none"
							stroke="currentColor"
							viewBox="0 0 24 24"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M5 13l4 4L19 7"
							></path>
						</svg>
					</div>
					<h2 class="font-heading mb-2 text-xl text-[var(--color-foreground)]">
						Signed In
					</h2>
					<p class="font-body text-sm text-[var(--color-muted-foreground)]">
						Redirecting you to the app...
					</p>
				</div>
			{:else}
				<div class="text-center">
					<div
						class="mx-auto mb-6 inline-flex h-16 w-16 items-center justify-center border-4 border-[var(--color-error)]"
					>
						<svg
							class="h-8 w-8 text-[var(--color-error)]"
							fill="none"
							stroke="currentColor"
							viewBox="0 0 24 24"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M6 18L18 6M6 6l12 12"
							></path>
						</svg>
					</div>
					<h2 class="font-heading mb-2 text-xl text-[var(--color-foreground)]">
						Verification Failed
					</h2>
					<p class="font-body mb-6 text-sm text-[var(--color-muted-foreground)]">
						{error ?? 'The magic link is invalid or expired.'}
					</p>
					<a
						href="/login"
						class="inline-block border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-6 py-3 font-mono text-sm uppercase tracking-wider text-[var(--color-primary-foreground)] shadow-brutal transition-all duration-200 hover:-translate-y-0.5"
					>
						Back to Login
					</a>
				</div>
			{/if}
		</div>
	</div>
</div>
