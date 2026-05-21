<script lang="ts">
	interface Props {
		title: string;
		value: string | number;
		subLabel?: string;
		accent?: 'default' | 'success' | 'warning' | 'error' | 'info';
	}

	let { title, value, subLabel, accent = 'default' }: Props = $props();

	const accentClass = $derived.by(() => {
		switch (accent) {
			case 'success':
				return 'bg-emerald-100';
			case 'warning':
				return 'bg-yellow-100';
			case 'error':
				return 'bg-red-100';
			case 'info':
				return 'bg-blue-100';
			default:
				return 'bg-white';
		}
	});
</script>

<div
	class="flex min-w-[140px] flex-col gap-1 border-2 border-[var(--color-foreground)] {accentClass} p-3 shadow-brutal"
>
	<span class="font-mono text-[10px] uppercase tracking-wider text-[var(--color-muted-foreground)]">
		{title}
	</span>
	<span class="font-heading text-2xl tracking-tight">{value}</span>
	{#if subLabel}
		<span class="font-mono text-[10px] uppercase tracking-wider text-[var(--color-muted-foreground)]">
			{subLabel}
		</span>
	{/if}
</div>
