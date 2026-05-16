<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		label: string;
		tooltip: string;
		variant?: 'default' | 'success' | 'destructive';
		size?: 'sm' | 'md';
		icon?: Snippet;
		fullWidth?: boolean;
	}

	let { label, tooltip, variant = 'default', size = 'md', icon, fullWidth = false }: Props = $props();

	const variantClass = $derived(
		{
			default: 'border-[var(--color-muted)] text-[var(--color-muted-foreground)]',
			success: 'border-[var(--color-success-brutal)] text-[var(--color-success-brutal)]',
			destructive: 'border-[var(--color-destructive)] text-[var(--color-destructive)]',
		}[variant]
	);

	const sizeClass = $derived(size === 'sm' ? 'px-3 py-1.5 text-xs' : 'px-6 py-4 text-sm');
</script>

<div class="group relative inline-block" class:flex-1={fullWidth} class:w-full={fullWidth}>
	<button
		type="button"
		aria-disabled="true"
		tabindex="0"
		title={tooltip}
		onclick={(e) => e.preventDefault()}
		class="flex w-full items-center justify-center gap-2 border-2 bg-white font-mono uppercase tracking-wider opacity-50 cursor-not-allowed {sizeClass} {variantClass}"
	>
		{#if icon}{@render icon()}{/if}
		{label}
	</button>
	<div
		role="tooltip"
		class="pointer-events-none absolute bottom-full left-1/2 z-20 mb-2 hidden w-64 -translate-x-1/2 border-2 border-[var(--color-foreground)] bg-white px-3 py-2 shadow-brutal group-hover:block group-focus-within:block"
	>
		<p class="font-mono text-xs text-[var(--color-foreground)]">{tooltip}</p>
	</div>
</div>
