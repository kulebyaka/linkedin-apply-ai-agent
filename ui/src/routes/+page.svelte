<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { reviewQueue } from '$lib/stores/reviewQueue.svelte';
	import JobCard from '$lib/components/review/JobCard.svelte';
	import DecisionButtons from '$lib/components/review/DecisionButtons.svelte';
	import NavigationControls from '$lib/components/review/NavigationControls.svelte';
	import FeedbackModal from '$lib/components/review/FeedbackModal.svelte';
	import EmptyState from '$lib/components/review/EmptyState.svelte';
	import ToastNotification from '$lib/components/ToastNotification.svelte';

	let modalType = $state<'decline' | 'retry' | null>(null);
	let showToast = $state(false);
	let toastMessage = $state('');
	let toastType = $state<'success' | 'error' | 'info'>('info');

	onMount(() => {
		reviewQueue.loadPending();
		window.addEventListener('keydown', handleKeyDown);
	});

	onDestroy(() => {
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
				if (reviewQueue.currentJob) handleApprove();
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
					? 'Application Approved'
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

	function handleApprove() {
		handleDecision('approved');
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
				{#if reviewQueue.totalCount > 0}
					<div
						class="border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-4 py-2 shadow-brutal"
					>
						<span class="font-mono text-sm font-semibold">{reviewQueue.totalCount} pending</span>
					</div>
				{/if}
			</div>
		</header>

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
					onApprove={handleApprove}
					onDecline={() => (modalType = 'decline')}
					onRetry={() => (modalType = 'retry')}
					isSubmitting={reviewQueue.isSubmitting}
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
						Keyboard: ← → to navigate | 1 decline | 2 retry | 3 approve
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
