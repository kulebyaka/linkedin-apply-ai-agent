<script lang="ts">
	import favicon from '$lib/assets/favicon.svg';
	import '../app.css';
	import { page } from '$app/stores';

	let { children } = $props();
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<div class="min-h-screen bg-[var(--color-background)]">
	<nav class="border-b-4 border-[var(--color-foreground)] bg-[var(--color-background)] px-4 py-3">
		<div class="container mx-auto flex items-center justify-between">
			<span class="font-heading text-lg font-bold">Job Application Agent</span>
			<div class="flex gap-4">
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
			</div>
		</div>
	</nav>

	<main>
		{@render children()}
	</main>
</div>
