<script lang="ts">
	import favicon from '$lib/assets/favicon.svg';
	import '../app.css';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { auth } from '$lib/stores/auth.svelte';

	let { children } = $props();

	const publicPaths = ['/login', '/auth/verify'];

	onMount(async () => {
		await auth.checkAuth();
	});

	$effect(() => {
		if (auth.loading) return;

		const pathname = $page.url.pathname;
		const isPublicPath = publicPaths.some((p) => pathname.startsWith(p));

		if (!auth.isAuthenticated && !isPublicPath) {
			goto('/login');
		}
	});

	async function handleLogout() {
		await auth.logout();
		goto('/login');
	}
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<div class="min-h-screen bg-[var(--color-background)]">
	{#if auth.loading}
		<div class="flex min-h-screen items-center justify-center">
			<p class="font-mono text-sm text-[var(--color-muted-foreground)]">Loading...</p>
		</div>
	{:else}
		<nav class="border-b-4 border-[var(--color-foreground)] bg-[var(--color-background)] px-4 py-3">
			<div class="container mx-auto flex items-center justify-between">
				<span class="font-heading text-lg font-bold">Job Application Agent</span>
				{#if auth.isAuthenticated}
					<div class="flex items-center gap-4">
						<a
							href="/"
							class="font-mono text-sm uppercase hover:underline"
							class:font-bold={$page.url.pathname === '/'}
						>
							Review
						</a>
						<a
							href="/generate"
							class="font-mono text-sm uppercase hover:underline"
							class:font-bold={$page.url.pathname === '/generate'}
						>
							Generate
						</a>
						<a
							href="/settings"
							class="font-mono text-sm uppercase hover:underline"
							class:font-bold={$page.url.pathname === '/settings'}
						>
							Settings
						</a>
						<span class="mx-2 text-[var(--color-muted)]">|</span>
						<span class="font-body text-sm text-[var(--color-muted-foreground)]">
							{auth.user?.display_name || auth.user?.email}
						</span>
						<button
							onclick={handleLogout}
							class="font-mono text-sm uppercase text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] hover:underline"
						>
							Logout
						</button>
					</div>
				{/if}
			</div>
		</nav>

		<main>
			{@render children()}
		</main>
	{/if}
</div>
