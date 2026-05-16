<script lang="ts">
	import WIPButton from '$lib/components/wip/WIPButton.svelte';
	import { WIP } from '$lib/wip/features';

	interface Props {
		onApprove: () => void;
		onDecline: () => void;
		onRetry: () => void;
		isSubmitting: boolean;
		applicationUrl?: string;
	}

	let { onApprove, onDecline, onRetry, isSubmitting, applicationUrl }: Props = $props();

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
