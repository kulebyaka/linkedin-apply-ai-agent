<script lang="ts">
	import WIPButton from '$lib/components/wip/WIPButton.svelte';
	import { WIP } from '$lib/wip/features';

	interface Props {
		onApprove: () => void;
		onDecline: () => void;
		onRetry: () => void;
		onDelete: () => void;
		isSubmitting: boolean;
		applicationUrl?: string;
	}

	let { onApprove, onDecline, onRetry, onDelete, isSubmitting, applicationUrl }: Props = $props();

	const DELETE_TOOLTIP =
		'Delete this job. It may reappear on the next LinkedIn search if it’s still in your results.';

	function handleDelete() {
		const confirmed = window.confirm(
			'Delete this job? It may reappear on the next LinkedIn search if it’s still in your results.'
		);
		if (confirmed) {
			onDelete();
		}
	}

	function handleMarkReviewedAndOpen() {
		if (applicationUrl) {
			window.open(applicationUrl, '_blank', 'noopener,noreferrer');
		}
		onApprove();
	}

	const baseClasses = `
		flex flex-1 items-center justify-center gap-2 border-2 px-6 py-4
		font-mono text-sm uppercase tracking-wider transition-all
		hover:-translate-y-0.5 disabled:opacity-50 disabled:pointer-events-none
	`;
</script>

<div class="flex flex-col gap-3">
	<!-- Decision row -->
	<div class="flex flex-col gap-3 sm:flex-row sm:gap-4">
		<!-- Decline Button -->
		<button
			onclick={onDecline}
			disabled={isSubmitting}
			class="{baseClasses}
			       border-[var(--color-destructive)] bg-[var(--color-background)] text-[var(--color-destructive)]
			       shadow-[4px_4px_0_var(--color-destructive)] hover:shadow-[6px_6px_0_var(--color-destructive)]"
		>
			<!-- X icon -->
			<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path
					stroke-linecap="round"
					stroke-linejoin="round"
					stroke-width="2"
					d="M6 18L18 6M6 6l12 12"
				/>
			</svg>
			Decline
		</button>

		<!-- Retry Button -->
		<button
			onclick={onRetry}
			disabled={isSubmitting}
			class="{baseClasses}
			       border-[var(--color-primary)] bg-[var(--color-background)]
			       shadow-[4px_4px_0_var(--color-primary)] hover:shadow-[6px_6px_0_var(--color-primary)]"
		>
			<!-- RotateCcw icon -->
			<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path
					stroke-linecap="round"
					stroke-linejoin="round"
					stroke-width="2"
					d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
				/>
			</svg>
			Retry
		</button>

		<!-- Approve Button (WIP — auto-apply not yet implemented) -->
		<WIPButton
			label="Approve"
			tooltip={WIP.AUTO_APPLY.tooltip}
			variant="success"
			fullWidth
		/>
	</div>

	<!-- Secondary actions row -->
	<div class="flex justify-end">
		<button
			type="button"
			onclick={handleDelete}
			disabled={isSubmitting}
			title={DELETE_TOOLTIP}
			aria-label="Delete job"
			class="flex items-center gap-1.5 border-2 border-[var(--color-muted-foreground)] bg-[var(--color-background)] px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)] transition-all hover:-translate-y-0.5 hover:border-[var(--color-destructive)] hover:text-[var(--color-destructive)] disabled:pointer-events-none disabled:opacity-50"
		>
			<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path
					stroke-linecap="round"
					stroke-linejoin="round"
					stroke-width="2"
					d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M1 7h22M9 7V4a1 1 0 011-1h4a1 1 0 011 1v3"
				/>
			</svg>
			Delete
		</button>
	</div>

	<!-- Manual apply CTA — replaces the inert Approve action for v1 -->
	{#if applicationUrl}
		<button
			onclick={handleMarkReviewedAndOpen}
			disabled={isSubmitting}
			class="flex w-full items-center justify-center gap-2 border-2 border-[var(--color-foreground)] bg-[var(--color-foreground)] px-6 py-3 font-mono text-sm uppercase tracking-wider text-[var(--color-primary)] shadow-brutal transition-all hover:-translate-y-0.5 disabled:opacity-50 disabled:pointer-events-none"
		>
			<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path
					stroke-linecap="round"
					stroke-linejoin="round"
					stroke-width="2"
					d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
				/>
			</svg>
			Mark Reviewed + Open in LinkedIn ↗
		</button>
	{/if}
</div>
