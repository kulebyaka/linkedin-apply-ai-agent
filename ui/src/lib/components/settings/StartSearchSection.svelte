<script lang="ts">
	import { auth } from '$lib/stores/auth.svelte';
	import { triggerLinkedInSearch } from '$lib/api/client';

	let triggering = $state(false);
	let triggered = $state(false);
	let showConfirmModal = $state(false);
	let error = $state<string | null>(null);

	const hasCv = $derived(auth.user?.master_cv_json != null);
	const hasSearchPrefs = $derived(auth.user?.search_preferences != null);
	const isReady = $derived(hasCv && hasSearchPrefs);

	const searchSummary = $derived.by(() => {
		const prefs = auth.user?.search_preferences;
		if (!prefs) return null;
		return {
			keywords: prefs.keywords || 'Any',
			location: prefs.location || 'Any',
		};
	});

	function handleClick() {
		error = null;
		showConfirmModal = true;
	}

	async function handleConfirm() {
		triggering = true;
		error = null;

		try {
			await triggerLinkedInSearch();
			triggered = true;
			showConfirmModal = false;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to start search';
		} finally {
			triggering = false;
		}
	}

	function handleCloseModal() {
		if (!triggering) {
			showConfirmModal = false;
		}
	}

	function handleBackdropClick(e: MouseEvent) {
		if (e.target === e.currentTarget) handleCloseModal();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') handleCloseModal();
	}
</script>

<svelte:window onkeydown={showConfirmModal ? handleKeydown : undefined} />

<section class="border-4 border-[var(--color-foreground)] bg-white p-6 shadow-brutal">
	<h2 class="font-heading mb-4 text-lg tracking-tight">Start the Process</h2>

	<!-- Readiness checklist -->
	<div class="mb-5 space-y-2">
		<div class="flex items-center gap-2.5">
			{#if hasCv}
				<svg class="h-5 w-5 shrink-0 text-[var(--color-success)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" />
				</svg>
				<span class="font-mono text-sm text-[var(--color-foreground)]">Master CV uploaded</span>
			{:else}
				<svg class="h-5 w-5 shrink-0 text-[var(--color-muted-foreground)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<circle cx="12" cy="12" r="9" stroke-width="2" />
				</svg>
				<span class="font-mono text-sm text-[var(--color-muted-foreground)]">Master CV uploaded</span>
			{/if}
		</div>

		<div class="flex items-center gap-2.5">
			{#if hasSearchPrefs}
				<svg class="h-5 w-5 shrink-0 text-[var(--color-success)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" />
				</svg>
				<span class="font-mono text-sm text-[var(--color-foreground)]">Search preferences configured</span>
			{:else}
				<svg class="h-5 w-5 shrink-0 text-[var(--color-muted-foreground)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<circle cx="12" cy="12" r="9" stroke-width="2" />
				</svg>
				<span class="font-mono text-sm text-[var(--color-muted-foreground)]">Search preferences configured</span>
			{/if}
		</div>
	</div>

	{#if error}
		<div class="mb-4 border-2 border-[var(--color-error)] bg-red-50 px-3 py-2 font-mono text-xs text-[var(--color-error)]">
			{error}
		</div>
	{/if}

	{#if triggered}
		<div class="border-2 border-[var(--color-success)] bg-emerald-50 px-4 py-3">
			<p class="mb-2 font-mono text-sm text-[var(--color-foreground)]">
				LinkedIn search started! Jobs will appear on the Review page as they're processed.
			</p>
			<a
				href="/"
				class="inline-block border-2 border-[var(--color-foreground)] bg-white px-4 py-2 font-mono text-xs uppercase tracking-wider text-[var(--color-foreground)] shadow-brutal transition-all duration-200 hover:-translate-y-0.5"
			>
				Go to Review
			</a>
		</div>
	{:else}
		<button
			onclick={handleClick}
			disabled={!isReady || triggering}
			class="border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-6 py-3 font-mono text-sm uppercase tracking-wider text-[var(--color-primary-foreground)] shadow-brutal transition-all duration-200 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
		>
			Start LinkedIn Search
		</button>
		{#if !isReady}
			<p class="mt-2 font-mono text-xs text-[var(--color-muted-foreground)]">
				Complete the steps above to enable searching.
			</p>
		{/if}
	{/if}
</section>

<!-- Confirmation Modal -->
{#if showConfirmModal}
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<div
		class="fixed inset-0 z-50 flex items-center justify-center p-4"
		onclick={handleBackdropClick}
		role="dialog"
		aria-modal="true"
		tabindex="-1"
	>
		<div class="absolute inset-0 bg-[var(--color-foreground)]/50 backdrop-blur-sm"></div>

		<div class="animate-slide-up relative w-full max-w-md border-4 border-[var(--color-foreground)] bg-[var(--color-background)] shadow-brutal-xl">
			<!-- Header -->
			<div class="flex items-center justify-between border-b-2 border-[var(--color-foreground)] px-6 py-4">
				<h2 class="font-heading text-lg font-bold">Start LinkedIn Search</h2>
				<button
					onclick={handleCloseModal}
					disabled={triggering}
					aria-label="Close"
					class="border-2 border-[var(--color-foreground)] p-1 transition-all hover:-translate-y-0.5 hover:bg-[var(--color-muted)] disabled:opacity-50"
				>
					<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			</div>

			<!-- Content -->
			<div class="p-6">
				<p class="mb-4 font-body text-sm text-[var(--color-muted-foreground)]">
					This will search LinkedIn for jobs matching your preferences and start processing matches. Results will appear on the Review page.
				</p>

				{#if searchSummary}
					<div class="border-2 border-[var(--color-muted)] bg-[var(--color-background)] px-3 py-2">
						<p class="font-mono text-xs text-[var(--color-muted-foreground)]">
							Keywords: <span class="font-bold text-[var(--color-foreground)]">{searchSummary.keywords}</span>
						</p>
						<p class="font-mono text-xs text-[var(--color-muted-foreground)]">
							Location: <span class="font-bold text-[var(--color-foreground)]">{searchSummary.location}</span>
						</p>
					</div>
				{/if}
			</div>

			<!-- Footer -->
			<div class="flex gap-3 border-t-2 border-[var(--color-foreground)] p-6">
				<button
					onclick={handleCloseModal}
					disabled={triggering}
					class="flex-1 border-2 border-[var(--color-foreground)] bg-[var(--color-background)] px-6 py-3 font-mono text-sm uppercase tracking-wider shadow-brutal transition-all hover:-translate-y-0.5 hover:bg-[var(--color-muted)] disabled:opacity-50"
				>
					Cancel
				</button>
				<button
					onclick={handleConfirm}
					disabled={triggering}
					class="flex-1 border-2 border-[var(--color-primary)] bg-[var(--color-primary)] px-6 py-3 font-mono text-sm uppercase tracking-wider text-[var(--color-primary-foreground)] shadow-brutal transition-all hover:-translate-y-0.5 disabled:pointer-events-none disabled:opacity-50"
				>
					{triggering ? 'Starting...' : 'Start Search'}
				</button>
			</div>
		</div>
	</div>
{/if}

<style>
	@keyframes slide-up {
		from {
			transform: translateY(10px);
			opacity: 0;
		}
		to {
			transform: translateY(0);
			opacity: 1;
		}
	}
	.animate-slide-up {
		animation: slide-up 0.3s ease-out;
	}
</style>
