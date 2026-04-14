<script lang="ts">
	import { requestMagicLink } from '$lib/api/auth';
	import { auth } from '$lib/stores/auth.svelte';
	import { goto } from '$app/navigation';

	let email = $state('');
	let isSubmitting = $state(false);
	let sent = $state(false);
	let error = $state<string | null>(null);

	// If already authenticated, redirect to home
	$effect(() => {
		if (!auth.loading && auth.isAuthenticated) {
			goto('/');
		}
	});

	async function handleSubmit(e: Event) {
		e.preventDefault();
		if (!email.trim()) return;

		isSubmitting = true;
		error = null;

		try {
			await requestMagicLink(email.trim());
			sent = true;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to send magic link';
		} finally {
			isSubmitting = false;
		}
	}
</script>

<svelte:head>
	<title>Login - Job Application Agent</title>
</svelte:head>

<div class="grain-texture min-h-screen bg-[var(--color-background)] px-4 py-16 sm:px-6 lg:px-8">
	<div class="mx-auto max-w-md">
		<div class="mb-12 text-center">
			<h1 class="font-heading mb-3 text-4xl tracking-tight text-[var(--color-foreground)]">
				Sign In
			</h1>
			<p class="font-body text-base text-[var(--color-muted-foreground)]">
				Enter your email to receive a magic link
			</p>
		</div>

		{#if sent}
			<div
				class="border-4 border-[var(--color-foreground)] bg-white p-8 shadow-brutal"
			>
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
								d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
							></path>
						</svg>
					</div>
					<h2 class="font-heading mb-2 text-xl text-[var(--color-foreground)]">
						Check Your Email
					</h2>
					<p class="font-body mb-6 text-sm text-[var(--color-muted-foreground)]">
						We sent a magic link to <span class="font-mono font-bold">{email}</span>.
						Click the link in the email to sign in.
					</p>
					<button
						onclick={() => { sent = false; error = null; }}
						class="font-mono text-sm uppercase tracking-wider text-[var(--color-muted-foreground)] underline hover:text-[var(--color-foreground)]"
					>
						Try a different email
					</button>
				</div>
			</div>
		{:else}
			<form
				onsubmit={handleSubmit}
				class="border-4 border-[var(--color-foreground)] bg-white p-8 shadow-brutal"
			>
				<div class="mb-6">
					<label
						for="email"
						class="font-mono mb-2 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
					>
						Email Address
					</label>
					<input
						id="email"
						type="email"
						bind:value={email}
						placeholder="you@example.com"
						required
						disabled={isSubmitting}
						class="font-body w-full border-2 border-[var(--color-foreground)] bg-white px-4 py-3 text-base text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50"
					/>
				</div>

				{#if error}
					<div
						class="mb-6 border-2 border-[var(--color-error)] bg-red-50 px-4 py-3 font-mono text-sm text-[var(--color-error)]"
					>
						{error}
					</div>
				{/if}

				<button
					type="submit"
					disabled={isSubmitting || !email.trim()}
					class="w-full border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-6 py-3 font-mono text-sm uppercase tracking-wider text-[var(--color-primary-foreground)] shadow-brutal transition-all duration-200 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
				>
					{isSubmitting ? 'Sending...' : 'Send Magic Link'}
				</button>
			</form>
		{/if}
	</div>
</div>
