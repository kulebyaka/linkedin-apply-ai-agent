<script lang="ts">
	interface Props {
		currentIndex: number;
		totalCount: number;
		hasPrevious: boolean;
		hasNext: boolean;
		onPrevious: () => void;
		onNext: () => void;
	}

	let { currentIndex, totalCount, hasPrevious, hasNext, onPrevious, onNext }: Props = $props();

	const buttonClasses = `
		flex items-center gap-2 border-2 border-[var(--color-foreground)] px-4 py-2
		font-mono text-sm uppercase tracking-wider transition-all
		hover:-translate-y-0.5 shadow-brutal hover:shadow-brutal-lg
		disabled:opacity-30 disabled:pointer-events-none disabled:shadow-none
	`;
</script>

<div class="flex items-center justify-center gap-4">
	<!-- Previous Button -->
	<button onclick={onPrevious} disabled={!hasPrevious} class={buttonClasses}>
		<!-- ChevronLeft icon -->
		<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
		</svg>
		Previous
	</button>

	<!-- Position Counter -->
	<div class="border-2 border-[var(--color-foreground)] bg-[var(--color-muted)] px-6 py-2">
		<span class="font-mono text-sm font-semibold">
			{currentIndex + 1} of {totalCount}
		</span>
	</div>

	<!-- Next Button -->
	<button onclick={onNext} disabled={!hasNext} class={buttonClasses}>
		Next
		<!-- ChevronRight icon -->
		<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
		</svg>
	</button>
</div>
