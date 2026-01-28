<script lang="ts">
	type Panel = 'job' | 'cv';

	interface Props {
		currentPanel: Panel;
		onPanelChange: (panel: Panel) => void;
	}

	let { currentPanel, onPanelChange }: Props = $props();
</script>

<div class="flex border-2 border-[var(--color-foreground)]">
	<button
		onclick={() => onPanelChange('job')}
		class="flex-1 px-6 py-3 font-mono text-sm uppercase tracking-wider transition-all
		       border-r-2 border-[var(--color-foreground)]
		       {currentPanel === 'job'
			? 'bg-[var(--color-primary)] text-[var(--color-primary-foreground)]'
			: 'bg-[var(--color-background)] hover:bg-[var(--color-muted)]'}"
	>
		Job Description
	</button>
	<button
		onclick={() => onPanelChange('cv')}
		class="flex-1 px-6 py-3 font-mono text-sm uppercase tracking-wider transition-all
		       {currentPanel === 'cv'
			? 'bg-[var(--color-primary)] text-[var(--color-primary-foreground)]'
			: 'bg-[var(--color-background)] hover:bg-[var(--color-muted)]'}"
	>
		CV Preview
	</button>
</div>
