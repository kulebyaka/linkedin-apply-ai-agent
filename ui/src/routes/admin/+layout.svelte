<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { auth } from '$lib/stores/auth.svelte';

	let { children } = $props();

	const navLinks: { href: string; label: string }[] = [
		{ href: '/admin/jobs', label: 'Jobs' },
		{ href: '/admin/queue', label: 'Queue' },
		{ href: '/admin/errors', label: 'Errors' },
		{ href: '/admin/users', label: 'Users' },
	];

	onMount(() => {
		if (!auth.loading && !auth.isAdmin) {
			goto('/');
		}
	});

	$effect(() => {
		if (auth.loading) return;
		if (!auth.isAdmin) {
			goto('/');
		}
	});
</script>

{#if auth.loading}
	<div class="flex min-h-[60vh] items-center justify-center">
		<p class="font-mono text-sm text-[var(--color-muted-foreground)]">Loading...</p>
	</div>
{:else if !auth.isAdmin}
	<div class="flex min-h-[60vh] items-center justify-center">
		<p class="font-mono text-sm text-[var(--color-muted-foreground)]">Redirecting...</p>
	</div>
{:else}
	<div class="container mx-auto flex flex-col gap-4 px-4 py-6 sm:flex-row">
		<aside
			class="w-full shrink-0 border-2 border-[var(--color-foreground)] bg-[var(--color-background)] p-4 shadow-brutal sm:w-48"
		>
			<h2 class="mb-3 font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
				Admin
			</h2>
			<nav class="flex flex-col gap-1">
				{#each navLinks as link}
					{@const active = $page.url.pathname.startsWith(link.href)}
					<a
						href={link.href}
						class="border-l-4 px-2 py-1.5 font-mono text-sm uppercase hover:underline"
						class:border-[var(--color-primary)]={active}
						class:font-bold={active}
						class:border-transparent={!active}
					>
						{link.label}
					</a>
				{/each}
			</nav>
		</aside>

		<section class="min-w-0 flex-1">
			{@render children()}
		</section>
	</div>
{/if}
