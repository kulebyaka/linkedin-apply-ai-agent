<script lang="ts">
	interface Props {
		onApprove: () => void;
		onDecline: () => void;
		onRetry: () => void;
		isSubmitting: boolean;
	}

	let { onApprove, onDecline, onRetry, isSubmitting }: Props = $props();

	const baseClasses = `
		flex flex-1 items-center justify-center gap-2 border-2 px-6 py-4
		font-mono text-sm uppercase tracking-wider transition-all
		hover:-translate-y-0.5 disabled:opacity-50 disabled:pointer-events-none
	`;
</script>

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
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"
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

	<!-- Approve Button -->
	<button
		onclick={onApprove}
		disabled={isSubmitting}
		class="{baseClasses}
		       border-[var(--color-success-brutal)] bg-[var(--color-success-brutal)] text-[var(--color-success-foreground)]
		       shadow-[4px_4px_0_hsl(142_71%_30%)] hover:shadow-[6px_6px_0_hsl(142_71%_30%)]"
	>
		<!-- Check icon -->
		<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
		</svg>
		Approve
	</button>
</div>
