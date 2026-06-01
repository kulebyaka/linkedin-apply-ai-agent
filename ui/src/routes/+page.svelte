<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { reviewQueue } from '$lib/stores/reviewQueue.svelte';
	import { inFlightStore } from '$lib/stores/inFlightJobs.svelte';
	import JobCard from '$lib/components/review/JobCard.svelte';
	import DecisionButtons from '$lib/components/review/DecisionButtons.svelte';
	import NavigationControls from '$lib/components/review/NavigationControls.svelte';
	import FeedbackModal from '$lib/components/review/FeedbackModal.svelte';
	import EmptyState from '$lib/components/review/EmptyState.svelte';
	import InFlightList from '$lib/components/review/InFlightList.svelte';
	import ToastNotification from '$lib/components/ToastNotification.svelte';

	let modalType = $state<'decline' | 'retry' | null>(null);
	let showToast = $state(false);
	let toastMessage = $state('');
	let toastType = $state<'success' | 'error' | 'info'>('info');

	onMount(() => {
		reviewQueue.loadPending();
		inFlightStore.loadInitial();
		inFlightStore.startPolling();
		window.addEventListener('keydown', handleKeyDown);
	});

	onDestroy(() => {
		inFlightStore.stopPolling();
		window.removeEventListener('keydown', handleKeyDown);
	});

	// Keyboard shortcuts
	function handleKeyDown(e: KeyboardEvent) {
		if (e.target instanceof HTMLTextAreaElement) return;
		if (modalType !== null) return; // Don't handle shortcuts when modal is open

		switch (e.key) {
			case 'ArrowLeft':
				reviewQueue.goToPrevious();
				break;
			case 'ArrowRight':
				reviewQueue.goToNext();
				break;
			case '1':
				if (reviewQueue.currentJob) modalType = 'decline';
				break;
			case '2':
				if (reviewQueue.currentJob) modalType = 'retry';
				break;
			case '3':
				if (reviewQueue.currentJob) handleMarkReviewedAndOpen();
				break;
		}
	}

	async function handleDecision(
		decision: 'approved' | 'declined' | 'retry',
		feedback?: string
	) {
		const result = await reviewQueue.submitDecision(decision, feedback);
		if (result) {
			showToastMessage(
				decision === 'approved'
					? 'Marked Reviewed — apply manually in the LinkedIn tab.'
					: decision === 'declined'
						? 'Application Declined'
						: 'CV Regeneration Started',
				'success'
			);
		} else if (reviewQueue.error) {
			showToastMessage(reviewQueue.error, 'error');
		}
		modalType = null;
	}

	function handleMarkReviewedAndOpen() {
		const url = reviewQueue.currentJob?.application_url;
		if (url) {
			window.open(url, '_blank', 'noopener,noreferrer');
		}
		handleDecision('approved');
	}

	async function handleDelete() {
		const ok = await reviewQueue.deleteCurrent();
		if (ok) {
			showToastMessage('Job deleted.', 'success');
		} else if (reviewQueue.error) {
			showToastMessage(reviewQueue.error, 'error');
		}
	}

	function handleModalSubmit(feedback: string) {
		if (modalType) {
			handleDecision(modalType === 'decline' ? 'declined' : 'retry', feedback);
		}
	}

	function showToastMessage(message: string, type: 'success' | 'error' | 'info') {
		toastMessage = message;
		toastType = type;
		showToast = true;
	}

	// Status counters shown next to "pending" — order matters (most useful first).
	// Each entry: [status key from BusinessState, display label, bg color var]
	const STAT_BADGES: Array<{ key: string; label: string; bg: string; fg?: string }> = [
		{ key: 'queued', label: 'queued', bg: 'var(--color-muted)' },
		{ key: 'processing', label: 'processing', bg: 'var(--color-accent)' },
		{ key: 'retrying', label: 'retrying', bg: 'var(--color-accent)' },
		{ key: 'scrape_failed', label: 'scrape retry', bg: 'var(--color-muted)' },
		{ key: 'applied', label: 'applied', bg: 'var(--color-secondary)' },
		{ key: 'approved', label: 'approved', bg: 'var(--color-secondary)' },
		{ key: 'declined', label: 'declined', bg: 'var(--color-muted)' },
		{ key: 'filtered_out', label: 'filtered out', bg: 'var(--color-muted)' },
		{ key: 'failed', label: 'failed', bg: 'var(--color-destructive)' },
	];

	const visibleStatBadges = $derived(
		STAT_BADGES.filter((b) => (reviewQueue.statusCounts[b.key] ?? 0) > 0)
	);
</script>

<svelte:head>
	<title>Review Applications - Job Application Agent</title>
</svelte:head>

<div class="grain-texture min-h-screen bg-[var(--color-background)]">
	<div class="container mx-auto max-w-4xl px-4 py-8">
		<!-- Header -->
		<header class="mb-8">
			<div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
				<div>
					<h1 class="font-heading text-2xl font-bold sm:text-3xl">Review Applications</h1>
					<p class="mt-1 font-body text-[var(--color-muted-foreground)]">
						Review AI-generated CVs and approve job applications
					</p>
				</div>
				<div class="flex flex-wrap items-center gap-2">
					{#if reviewQueue.totalCount > 0}
						<div
							class="border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-4 py-2 shadow-brutal"
							title="Jobs awaiting your review"
						>
							<span class="font-mono text-sm font-semibold"
								>{reviewQueue.totalCount} pending</span
							>
						</div>
					{/if}
					{#each visibleStatBadges as badge (badge.key)}
						<div
							class="border-2 border-[var(--color-foreground)] px-3 py-1.5"
							style:background-color={badge.bg}
							title="Jobs in '{badge.key}' state"
						>
							<span class="font-mono text-xs font-semibold">
								{reviewQueue.statusCounts[badge.key]}
								{badge.label}
							</span>
						</div>
					{/each}
				</div>
			</div>
		</header>

		<!-- In-flight (read-only, polled) -->
		<InFlightList jobs={inFlightStore.jobs} />

		<!-- Main Content -->
		{#if reviewQueue.isLoading}
			<div class="flex min-h-[500px] items-center justify-center">
				<div
					class="animate-pulse border-4 border-[var(--color-foreground)] bg-[var(--color-background)] p-8 shadow-brutal"
				>
					<p class="font-mono text-sm uppercase tracking-wider">Loading applications...</p>
				</div>
			</div>
		{:else if reviewQueue.totalCount === 0}
			<EmptyState />
		{:else if reviewQueue.currentJob}
			<div class="space-y-6">
				<JobCard job={reviewQueue.currentJob} />

				<DecisionButtons
					onApprove={() => handleDecision('approved')}
					onDecline={() => (modalType = 'decline')}
					onRetry={() => (modalType = 'retry')}
					onDelete={handleDelete}
					isSubmitting={reviewQueue.isSubmitting}
					applicationUrl={reviewQueue.currentJob.application_url}
				/>

				<NavigationControls
					currentIndex={reviewQueue.currentIndex}
					totalCount={reviewQueue.totalCount}
					hasPrevious={reviewQueue.hasPrevious}
					hasNext={reviewQueue.hasNext}
					onPrevious={reviewQueue.goToPrevious}
					onNext={reviewQueue.goToNext}
				/>

				<!-- Keyboard shortcuts hint -->
				<div class="text-center">
					<p class="font-mono text-xs text-[var(--color-muted-foreground)]">
						Keyboard: ← → to navigate | 1 decline | 2 retry | 3 mark reviewed + open in LinkedIn
					</p>
				</div>
			</div>
		{/if}

		<!-- Feedback Modal -->
		<FeedbackModal
			isOpen={modalType !== null}
			type={modalType || 'decline'}
			isSubmitting={reviewQueue.isSubmitting}
			onClose={() => (modalType = null)}
			onSubmit={handleModalSubmit}
		/>
	</div>
</div>

<!-- Toast -->
{#if showToast}
	<ToastNotification message={toastMessage} type={toastType} onClose={() => (showToast = false)} />
{/if}
