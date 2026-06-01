<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import {
		triggerLinkedInSearch,
		getLinkedInSearchStatus,
		type UserLastRun,
	} from '$lib/api/client';

	let triggering = $state(false);
	let showConfirmModal = $state(false);
	let error = $state<string | null>(null);
	let lastRun = $state<UserLastRun | null>(null);
	let polling = $state(false);
	let searchRunning = $state(false);
	let nextRunTime = $state<Date | null>(null);
	let scheduleEnabled = $state(false);
	let initialLoad = $state(true);
	let now = $state(Date.now());

	let pollAbort: AbortController | null = null;
	let clockInterval: ReturnType<typeof setInterval> | null = null;

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

	const lastRunRelative = $derived.by(() => {
		now; // re-render on tick
		return lastRun ? relativeTime(new Date(lastRun.time)) : null;
	});
	const nextRunRelative = $derived.by(() => {
		now;
		return nextRunTime ? relativeTime(nextRunTime) : null;
	});

	function relativeTime(date: Date): string {
		const diffMs = date.getTime() - Date.now();
		const past = diffMs < 0;
		const absMin = Math.round(Math.abs(diffMs) / 60_000);
		if (absMin < 1) return past ? 'just now' : 'in less than a minute';
		if (absMin < 60) return past ? `${absMin} min ago` : `in ${absMin} min`;
		const absHr = Math.round(absMin / 60);
		if (absHr < 24) return past ? `${absHr} h ago` : `in ${absHr} h`;
		const absDay = Math.round(absHr / 24);
		return past ? `${absDay} d ago` : `in ${absDay} d`;
	}

	onMount(async () => {
		try {
			const status = await getLinkedInSearchStatus();
			scheduleEnabled = status.enabled;
			nextRunTime = status.next_run_time ? new Date(status.next_run_time) : null;
			lastRun = status.user_last_run;
			searchRunning = status.running;
			if (searchRunning) {
				// A search is already in flight (scheduled or another tab) — attach polling.
				const since = status.last_run_time
					? new Date(status.last_run_time)
					: new Date(Date.now() - 60_000);
				pollForOutcome(since);
			}
		} catch {
			// Best-effort hydrate; ignore failures.
		} finally {
			initialLoad = false;
		}
		clockInterval = setInterval(() => (now = Date.now()), 30_000);
	});

	onDestroy(() => {
		if (clockInterval) clearInterval(clockInterval);
		pollAbort?.abort();
	});

	function handleClick() {
		error = null;
		showConfirmModal = true;
	}

	async function handleConfirm() {
		triggering = true;
		error = null;
		const triggerStart = new Date();
		try {
			await triggerLinkedInSearch();
			searchRunning = true;
			showConfirmModal = false;
			pollForOutcome(triggerStart);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to start search';
		} finally {
			triggering = false;
		}
	}

	async function pollForOutcome(triggerStart: Date) {
		pollAbort?.abort();
		const abort = new AbortController();
		pollAbort = abort;
		polling = true;
		const deadline = Date.now() + 180_000; // 3 min ceiling
		try {
			while (Date.now() < deadline && !abort.signal.aborted) {
				await new Promise((r) => setTimeout(r, 2000));
				if (abort.signal.aborted) return;
				try {
					const status = await getLinkedInSearchStatus();
					searchRunning = status.running;
					nextRunTime = status.next_run_time ? new Date(status.next_run_time) : null;
					const run = status.user_last_run;
					if (run && new Date(run.time) >= triggerStart) {
						lastRun = run;
						searchRunning = false;
						return;
					}
				} catch {
					// transient — keep polling
				}
			}
		} finally {
			polling = false;
		}
	}

	function reasonLabel(reason: UserLastRun['reason']): string {
		switch (reason) {
			case 'ok':
				return 'Completed';
			case 'no_results':
				return 'LinkedIn returned no results';
			case 'no_users':
				return 'Search preferences not configured';
			case 'scrape_failed':
				return 'Scrape failed';
			case 'auth_failed':
				return 'LinkedIn authentication failed';
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

	<!-- Last run result (persists across refresh) -->
	{#if lastRun && !searchRunning}
		{#if lastRun.reason === 'ok'}
			<div class="mb-4 border-2 border-[var(--color-success)] bg-emerald-50 px-4 py-3">
				<p class="mb-1 font-mono text-sm text-[var(--color-foreground)]">
					Last search: <strong>{lastRun.enqueued}</strong> new job{lastRun.enqueued === 1 ? '' : 's'}
					{#if lastRun.deduped > 0}
						<span class="text-[var(--color-muted-foreground)]">
							· {lastRun.deduped} already seen · {lastRun.jobs_found} scraped
						</span>
					{/if}
					{#if lastRunRelative}<span class="text-[var(--color-muted-foreground)]"> · {lastRunRelative}</span>{/if}
				</p>
				<a
					href="/"
					class="mt-2 inline-block border-2 border-[var(--color-foreground)] bg-white px-4 py-2 font-mono text-xs uppercase tracking-wider text-[var(--color-foreground)] shadow-brutal transition-all duration-200 hover:-translate-y-0.5"
				>
					Go to Review
				</a>
			</div>
		{:else}
			<div class="mb-4 border-2 border-[var(--color-error)] bg-amber-50 px-4 py-3">
				<p class="mb-2 font-mono text-sm font-bold text-[var(--color-foreground)]">
					Last search: {reasonLabel(lastRun.reason)} — 0 jobs found
					{#if lastRunRelative}<span class="font-normal text-[var(--color-muted-foreground)]"> · {lastRunRelative}</span>{/if}
				</p>
				{#if lastRun.reason === 'no_results'}
					<p class="mb-2 font-mono text-xs text-[var(--color-foreground)]">
						Open the exact query LinkedIn saw and verify in a browser — if you also see "No matching jobs found" there, tweak your search preferences (e.g. use a valid country like "Germany" instead of "EU").
					</p>
				{/if}
				{#if lastRun.message}
					<p class="mb-2 font-mono text-xs text-[var(--color-foreground)]">{lastRun.message}</p>
				{/if}
				{#if lastRun.search_url}
					<a
						href={lastRun.search_url}
						target="_blank"
						rel="noopener noreferrer"
						class="inline-block break-all border-2 border-[var(--color-foreground)] bg-white px-3 py-2 font-mono text-xs text-[var(--color-foreground)] shadow-brutal transition-all duration-200 hover:-translate-y-0.5"
					>
						Open LinkedIn search URL ↗
					</a>
				{/if}
			</div>
		{/if}
	{/if}

	<!-- In-progress state (covers manual + scheduled runs) -->
	{#if searchRunning}
		<div class="mb-4 flex items-center gap-3 border-2 border-[var(--color-foreground)] bg-white px-4 py-3">
			<svg class="h-5 w-5 shrink-0 animate-spin text-[var(--color-foreground)]" fill="none" viewBox="0 0 24 24">
				<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
				<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"></path>
			</svg>
			<p class="font-mono text-sm text-[var(--color-foreground)]">
				LinkedIn search in progress… results will appear on the Review page.
			</p>
		</div>
	{:else if polling}
		<div class="mb-4 border-2 border-[var(--color-foreground)] bg-white px-4 py-3">
			<p class="font-mono text-sm text-[var(--color-foreground)]">
				Stopped watching after a few minutes. The search may still be running on the server — check the Review page or refresh this section.
			</p>
		</div>
	{/if}

	<!-- Action button + schedule footnote -->
	<button
		onclick={handleClick}
		disabled={!isReady || triggering || searchRunning || initialLoad}
		class="border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-6 py-3 font-mono text-sm uppercase tracking-wider text-[var(--color-primary-foreground)] shadow-brutal transition-all duration-200 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
	>
		{#if searchRunning}
			Search running…
		{:else if lastRun}
			Run another search
		{:else}
			Start LinkedIn Search
		{/if}
	</button>

	{#if !isReady}
		<p class="mt-2 font-mono text-xs text-[var(--color-muted-foreground)]">
			Complete the steps above to enable searching.
		</p>
	{:else if scheduleEnabled && nextRunRelative && !searchRunning}
		<p class="mt-2 font-mono text-xs text-[var(--color-muted-foreground)]">
			Searches also run automatically — next: {nextRunRelative}.
		</p>
	{:else if scheduleEnabled && !searchRunning}
		<p class="mt-2 font-mono text-xs text-[var(--color-muted-foreground)]">
			Searches also run automatically on a schedule.
		</p>
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
