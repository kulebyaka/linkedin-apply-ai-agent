<script lang="ts">
	import type { ModelCatalogEntry } from '$lib/api/auth';

	interface Props {
		/** Catalog entries to pick from (already filtered for the operation). */
		catalog: ModelCatalogEntry[];
		/** Selected provider (two-way bound). */
		provider: string;
		/** Selected model (two-way bound). */
		model: string;
		disabled?: boolean;
		/** Prefix for element ids: `${idPrefix}-provider-select` / `-model-select`. */
		idPrefix: string;
		providerLabel?: string;
		modelLabel?: string;
		/** Append the per-1M pricing to each model option. */
		showCost?: boolean;
	}

	let {
		catalog,
		provider = $bindable(''),
		model = $bindable(''),
		disabled = false,
		idPrefix,
		providerLabel = 'LLM Provider',
		modelLabel = 'Model',
		showCost = true,
	}: Props = $props();

	const PROVIDER_LABELS: Record<string, string> = {
		openai: 'OpenAI',
		anthropic: 'Anthropic',
		deepseek: 'DeepSeek',
		grok: 'Grok',
	};

	// Distinct providers, preserving catalog order.
	const providers = $derived([...new Set(catalog.map((e) => e.provider))]);

	// Models available for the currently-selected provider.
	const modelsForProvider = $derived(catalog.filter((e) => e.provider === provider));

	function providerName(p: string): string {
		return PROVIDER_LABELS[p] ?? p;
	}

	function modelText(entry: ModelCatalogEntry): string {
		if (!showCost) return entry.display_name;
		return `${entry.display_name} ($${entry.input_cost_per_1m} / $${entry.output_cost_per_1m} per 1M)`;
	}

	// Keep the selected model consistent with the selected provider: whenever
	// the provider changes (or the catalog loads), snap the model to the first
	// entry of that provider unless the current model is still valid.
	$effect(() => {
		const models = catalog.filter((e) => e.provider === provider);
		if (models.length > 0 && !models.some((e) => e.model === model)) {
			model = models[0].model;
		}
	});
</script>

<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
	<div>
		<label
			for={`${idPrefix}-provider-select`}
			class="mb-2 block font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
		>
			{providerLabel}
		</label>
		<div class="relative">
			<select
				id={`${idPrefix}-provider-select`}
				bind:value={provider}
				{disabled}
				class="w-full appearance-none border-2 border-[var(--color-foreground)] bg-white px-4 py-3 pr-10 font-mono text-sm text-[var(--color-foreground)] shadow-brutal transition-all duration-200 focus:border-[var(--color-primary)] focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
			>
				{#each providers as p (p)}
					<option value={p}>{providerName(p)}</option>
				{/each}
			</select>
			<div class="pointer-events-none absolute inset-y-0 right-0 flex items-center px-3 text-[var(--color-foreground)]">
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
				</svg>
			</div>
		</div>
	</div>

	<div>
		<label
			for={`${idPrefix}-model-select`}
			class="mb-2 block font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
		>
			{modelLabel}
		</label>
		<div class="relative">
			<select
				id={`${idPrefix}-model-select`}
				bind:value={model}
				disabled={disabled || modelsForProvider.length === 0}
				class="w-full appearance-none border-2 border-[var(--color-foreground)] bg-white px-4 py-3 pr-10 font-mono text-sm text-[var(--color-foreground)] shadow-brutal transition-all duration-200 focus:border-[var(--color-primary)] focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
			>
				{#each modelsForProvider as entry (entry.model)}
					<option value={entry.model}>{modelText(entry)}</option>
				{/each}
			</select>
			<div class="pointer-events-none absolute inset-y-0 right-0 flex items-center px-3 text-[var(--color-foreground)]">
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
				</svg>
			</div>
		</div>
	</div>
</div>
