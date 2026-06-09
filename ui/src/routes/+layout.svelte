<script lang="ts">
	import favicon from '$lib/assets/favicon.svg';
	import '../app.css';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import WIPBadge from '$lib/components/wip/WIPBadge.svelte';
	import { WIP } from '$lib/wip/features';
	import NotificationBell from '$lib/components/NotificationBell.svelte';

	let { children } = $props();

	const appVersion = __APP_VERSION__;

	const publicPaths = ['/login', '/auth/verify', '/welcome'];

	let menuOpen = $state(false);

	onMount(async () => {
		await auth.checkAuth();
	});

	$effect(() => {
		// Close menu on route change
		menuOpen = false;
	});

	$effect(() => {
		if (auth.loading) return;

		const pathname = $page.url.pathname;
		const isPublicPath = publicPaths.some((p) => pathname.startsWith(p));

		if (!auth.isAuthenticated && !isPublicPath) {
			goto('/welcome');
		}
	});

	async function handleLogout() {
		menuOpen = false;
		await auth.logout();
		goto('/login');
	}

	const navLinks: { href: string; label: string; wip?: { label: string; tooltip: string } }[] = [
		{ href: '/', label: 'Review' },
		{ href: '/generate', label: 'Generate', wip: WIP.GENERATE_PAGE_SCOPE },
		{ href: '/applications', label: 'Applications' },
		{ href: '/settings', label: 'Settings' },
		{ href: '/welcome', label: 'Guide' },
	];
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
		<nav class="border-b-4 border-[var(--color-foreground)] bg-[var(--color-background)]">
			<div class="container mx-auto flex items-center justify-between px-4 py-3">
				<div class="flex items-center gap-2">
					<a href="/" class="flex items-center gap-2 font-heading text-lg font-bold hover:underline">
						Job Application Agent
						<WIPBadge label={WIP.V1_BETA.label} />
					</a>
					<!-- Release version: blends into the nav background, revealed only on text selection.
					     Kept outside the <a> so the link's hover:underline doesn't decorate it. -->
					<span class="font-mono text-xs text-[var(--color-background)] select-text" title="Release version"
						>{appVersion}</span
					>
				</div>

				{#if auth.isAuthenticated}
					<!-- Desktop nav -->
					<div class="hidden items-center gap-4 sm:flex">
						{#each navLinks as link}
							<a
								href={link.href}
								class="flex items-center gap-1.5 font-mono text-sm uppercase hover:underline"
								class:font-bold={$page.url.pathname === link.href}
							>
								{link.label}
								{#if link.wip}
									<WIPBadge label={link.wip.label} tooltip={link.wip.tooltip} />
								{/if}
							</a>
						{/each}
						<span class="text-[var(--color-muted)]">|</span>
						<NotificationBell />
						<span class="font-body max-w-[120px] truncate text-sm text-[var(--color-muted-foreground)]">
							{auth.user?.display_name || auth.user?.email}
						</span>
						<button
							onclick={handleLogout}
							class="font-mono text-sm uppercase text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] hover:underline"
						>
							Logout
						</button>
					</div>

					<!-- Mobile: notification bell + hamburger -->
					<div class="flex items-center gap-2 sm:hidden">
						<NotificationBell />
						<button
						onclick={() => (menuOpen = !menuOpen)}
						class="flex h-9 w-9 flex-col items-center justify-center gap-1.5 border-2 border-[var(--color-foreground)] bg-[var(--color-background)] shadow-brutal"
						aria-label="Toggle menu"
						aria-expanded={menuOpen}
					>
						<span
							class="block h-0.5 w-4 bg-[var(--color-foreground)] transition-all duration-200"
							class:rotate-45={menuOpen}
							class:translate-y-2={menuOpen}
						></span>
						<span
							class="block h-0.5 w-4 bg-[var(--color-foreground)] transition-all duration-200"
							class:opacity-0={menuOpen}
						></span>
						<span
							class="block h-0.5 w-4 bg-[var(--color-foreground)] transition-all duration-200"
							class:-rotate-45={menuOpen}
							class:-translate-y-2={menuOpen}
						></span>
					</button>
				</div>
				{:else}
					<!-- Logged-out: single Sign In action (no hamburger needed) -->
					<a
						href="/login"
						class="border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-4 py-2 font-mono text-sm uppercase tracking-wider shadow-brutal transition-all duration-150 hover:-translate-y-0.5"
						class:font-bold={$page.url.pathname === '/login'}
					>
						Sign In
					</a>
				{/if}
			</div>

			<!-- Mobile dropdown menu -->
			{#if auth.isAuthenticated && menuOpen}
				<div class="border-t-2 border-[var(--color-foreground)] bg-[var(--color-background)] sm:hidden">
					<div class="container mx-auto px-4 py-2">
						{#each navLinks as link}
							<a
								href={link.href}
								onclick={() => (menuOpen = false)}
								class="flex items-center gap-2 border-b border-[var(--color-muted)] py-3 font-mono text-sm uppercase"
								class:font-bold={$page.url.pathname === link.href}
								class:text-[var(--color-primary)]={$page.url.pathname === link.href}
							>
								{link.label}
								{#if link.wip}
									<WIPBadge label={link.wip.label} tooltip={link.wip.tooltip} />
								{/if}
							</a>
						{/each}
						<div class="py-3">
							<p class="font-body mb-2 truncate text-xs text-[var(--color-muted-foreground)]">
								{auth.user?.display_name || auth.user?.email}
							</p>
							<button
								onclick={handleLogout}
								class="font-mono text-sm uppercase text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] hover:underline"
							>
								Logout
							</button>
						</div>
					</div>
				</div>
			{/if}
		</nav>

		<main>
			{@render children()}
		</main>
	{/if}
</div>
