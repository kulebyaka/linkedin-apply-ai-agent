<script lang="ts">
	interface Props {
		isOpen: boolean;
		type: 'decline' | 'retry';
		isSubmitting: boolean;
		onClose: () => void;
		onSubmit: (feedback: string) => void;
	}

	let { isOpen, type, isSubmitting, onClose, onSubmit }: Props = $props();

	let feedback = $state('');

	const isRetry = $derived(type === 'retry');
	const title = $derived(isRetry ? 'Regenerate CV' : 'Decline Application');
	const description = $derived(
		isRetry
			? 'What should be changed? (feedback for AI regeneration)'
			: 'Why are you declining this job? (optional note for your records)'
	);
	const submitText = $derived(isRetry ? 'Regenerate CV' : 'Confirm Decline');
	const isSubmitDisabled = $derived(isRetry && !feedback.trim());

	function handleSubmit() {
		onSubmit(feedback);
		feedback = '';
	}

	function handleClose() {
		feedback = '';
		onClose();
	}

	function handleBackdropClick(e: MouseEvent) {
		if (e.target === e.currentTarget) handleClose();
	}
</script>

{#if isOpen}
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<div
		class="fixed inset-0 z-50 flex items-center justify-center p-4"
		onclick={handleBackdropClick}
		role="dialog"
		aria-modal="true"
		tabindex="-1"
	>
		<!-- Backdrop overlay -->
		<div class="absolute inset-0 bg-[var(--color-foreground)]/50 backdrop-blur-sm"></div>

		<!-- Modal Content -->
		<div
			class="animate-slide-up relative w-full max-w-md border-4 border-[var(--color-foreground)] bg-[var(--color-background)] shadow-brutal-xl"
		>
			<!-- Header -->
			<div
				class="flex items-center justify-between border-b-2 border-[var(--color-foreground)] px-6 py-4"
			>
				<h2 class="font-heading text-lg font-bold">{title}</h2>
				<button
					onclick={handleClose}
					class="border-2 border-[var(--color-foreground)] p-1 transition-all hover:-translate-y-0.5 hover:bg-[var(--color-muted)]"
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
				</button>
			</div>

			<!-- Content -->
			<div class="p-6">
				<p class="mb-4 font-body text-sm text-[var(--color-muted-foreground)]">{description}</p>
				<textarea
					bind:value={feedback}
					placeholder={isRetry
						? 'Please emphasize Python skills and reduce Java experience...'
						: 'Not a good fit for my experience level...'}
					class="min-h-[120px] w-full border-2 border-[var(--color-foreground)] bg-[var(--color-background)]
					       p-4 font-body text-sm placeholder:text-[var(--color-muted-foreground)]
					       focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
				></textarea>
			</div>

			<!-- Footer -->
			<div class="flex gap-3 border-t-2 border-[var(--color-foreground)] p-6">
				<button
					onclick={handleClose}
					disabled={isSubmitting}
					class="flex-1 border-2 border-[var(--color-foreground)] bg-[var(--color-background)]
					       px-6 py-3 font-mono text-sm uppercase tracking-wider
					       shadow-brutal transition-all hover:-translate-y-0.5 hover:bg-[var(--color-muted)]
					       disabled:opacity-50"
				>
					Cancel
				</button>
				<button
					onclick={handleSubmit}
					disabled={isSubmitDisabled || isSubmitting}
					class="flex-1 border-2 px-6 py-3 font-mono text-sm uppercase tracking-wider
					       shadow-brutal transition-all hover:-translate-y-0.5
					       disabled:pointer-events-none disabled:opacity-50
					       {isRetry
						? 'border-[var(--color-primary)] bg-[var(--color-primary)] text-[var(--color-primary-foreground)]'
						: 'border-[var(--color-destructive)] bg-[var(--color-destructive)] text-[var(--color-destructive-foreground)]'}"
				>
					{isSubmitting ? 'Submitting...' : submitText}
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
