<script lang="ts">
	interface Props {
		label?: string;
		tooltip?: string;
		size?: 'sm' | 'md';
		tone?: 'amber' | 'muted';
		placement?: 'top' | 'bottom';
	}

	let {
		label = 'WIP',
		tooltip,
		size = 'sm',
		tone = 'amber',
		placement = 'bottom'
	}: Props = $props();

	const sizeClass = $derived(size === 'sm' ? 'px-1.5 py-0.5 text-[10px]' : 'px-2 py-1 text-xs');
	const toneClass = $derived(
		tone === 'amber'
			? 'border-[var(--color-foreground)] bg-[var(--color-primary)] text-[var(--color-foreground)]'
			: 'border-[var(--color-muted)] bg-white text-[var(--color-muted-foreground)]'
	);
	const placementClass = $derived(
		placement === 'top' ? 'bottom-full mb-2' : 'top-full mt-2'
	);
</script>

<span class="group relative inline-flex items-center">
	<span
		class="inline-flex items-center border font-mono font-bold uppercase tracking-wider {sizeClass} {toneClass}"
		aria-label={tooltip ?? label}
	>
		{label}
	</span>
	{#if tooltip}
		<span
			role="tooltip"
			class="pointer-events-none absolute left-1/2 z-50 hidden w-56 -translate-x-1/2 border-2 border-[var(--color-foreground)] bg-white px-3 py-2 normal-case shadow-brutal group-hover:block group-focus-within:block {placementClass}"
		>
			<span class="font-mono text-xs text-[var(--color-foreground)]">{tooltip}</span>
		</span>
	{/if}
</span>
