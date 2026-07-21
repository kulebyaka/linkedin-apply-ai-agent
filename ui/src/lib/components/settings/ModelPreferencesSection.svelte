<script lang="ts">
	import { onMount } from 'svelte';
	import type {
		LLMOperation,
		ModelCatalogEntry,
		ModelChoice,
		UserModelPreferences,
	} from '$lib/api/auth';
	import { getModelCatalog, updateModelPreferences } from '$lib/api/settings';
	import { auth } from '$lib/stores/auth.svelte';
	import ModelSelector from '$lib/components/ModelSelector.svelte';

	interface Slot {
		key: keyof UserModelPreferences;
		operation: LLMOperation;
		label: string;
		description: string;
	}

	const SLOTS: Slot[] = [
		{
			key: 'cv_generation',
			operation: 'cv_generation',
			label: 'CV Generation',
			description: 'Used for tailoring your CV to each job posting.',
		},
		{
			key: 'job_filtering',
			operation: 'job_filtering',
			label: 'Job Filtering',
			description: 'Used for scoring each LinkedIn job posting.',
		},
		{
			key: 'filter_prompt_generation',
			operation: 'filter_prompt_generation',
			label: 'Filter Prompt Generation',
			description: 'Used when you click "Generate Prompt" in Job Filter settings.',
		},
	];

	let catalogs = $state<Record<LLMOperation, ModelCatalogEntry[]>>({
		cv_generation: [],
		job_filtering: [],
		filter_prompt_generation: [],
	});

	// Selected provider + model per slot, bound to the two ModelSelector dropdowns.
	let selectedProvider = $state<Record<keyof UserModelPreferences, string>>({
		cv_generation: '',
		job_filtering: '',
		filter_prompt_generation: '',
	});
	let selectedModel = $state<Record<keyof UserModelPreferences, string>>({
		cv_generation: '',
		job_filtering: '',
		filter_prompt_generation: '',
	});

	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let saving = $state(false);
	let saved = $state(false);
	let error = $state<string | null>(null);

	function inCatalog(catalog: ModelCatalogEntry[], choice: ModelChoice): boolean {
		return catalog.some((e) => e.provider === choice.provider && e.model === choice.model);
	}

	function resolveInitial(
		catalog: ModelCatalogEntry[],
		current: ModelChoice | null | undefined,
		fallback: ModelChoice,
	): { provider: string; model: string } {
		// Stored preference wins if still in the catalog.
		if (current && inCatalog(catalog, current)) return current;
		// Otherwise the global .env default if present, else the first entry so
		// the dropdowns are never empty.
		if (inCatalog(catalog, fallback)) return fallback;
		if (catalog.length > 0) return { provider: catalog[0].provider, model: catalog[0].model };
		return { provider: '', model: '' };
	}

	onMount(async () => {
		try {
			const [cv, jf, fp] = await Promise.all([
				getModelCatalog('cv_generation'),
				getModelCatalog('job_filtering'),
				getModelCatalog('filter_prompt_generation'),
			]);
			catalogs = {
				cv_generation: cv.models,
				job_filtering: jf.models,
				filter_prompt_generation: fp.models,
			};

			const prefs = auth.user?.model_preferences ?? null;
			const initial = {
				cv_generation: resolveInitial(cv.models, prefs?.cv_generation, cv.default),
				job_filtering: resolveInitial(jf.models, prefs?.job_filtering, jf.default),
				filter_prompt_generation: resolveInitial(
					fp.models,
					prefs?.filter_prompt_generation,
					fp.default,
				),
			};
			selectedProvider = {
				cv_generation: initial.cv_generation.provider,
				job_filtering: initial.job_filtering.provider,
				filter_prompt_generation: initial.filter_prompt_generation.provider,
			};
			selectedModel = {
				cv_generation: initial.cv_generation.model,
				job_filtering: initial.job_filtering.model,
				filter_prompt_generation: initial.filter_prompt_generation.model,
			};
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Could not load model catalog';
		} finally {
			loading = false;
		}
	});

	function toChoice(key: keyof UserModelPreferences): ModelChoice | null {
		const provider = selectedProvider[key];
		const model = selectedModel[key];
		if (!provider || !model) return null;
		return { provider: provider as ModelChoice['provider'], model };
	}

	async function handleSave() {
		saving = true;
		error = null;
		saved = false;

		const payload: UserModelPreferences = {
			cv_generation: toChoice('cv_generation'),
			job_filtering: toChoice('job_filtering'),
			filter_prompt_generation: toChoice('filter_prompt_generation'),
		};

		try {
			const updated = await updateModelPreferences(payload);
			auth.setUser(updated);
			saved = true;
			setTimeout(() => (saved = false), 2000);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to save model preferences';
		} finally {
			saving = false;
		}
	}
</script>

<section class="border-4 border-[var(--color-foreground)] bg-white p-6 shadow-brutal">
	<h2 class="font-heading mb-2 text-lg tracking-tight">LLM Model Preferences</h2>
	<p class="font-mono mb-4 text-xs text-[var(--color-muted-foreground)]">
		Pick which model to use for each operation. Costs shown are per 1 million tokens.
	</p>

	{#if loading}
		<p class="font-mono text-xs text-[var(--color-muted-foreground)]">Loading models…</p>
	{:else if loadError}
		<div class="mb-4 border-2 border-[var(--color-error)] bg-red-50 px-3 py-2 font-mono text-xs text-[var(--color-error)]">
			{loadError}
		</div>
	{:else}
		<div class="flex flex-col gap-4">
			{#each SLOTS as slot (slot.key)}
				<div>
					<h3 class="font-mono mb-2 block text-xs font-semibold uppercase tracking-wider text-[var(--color-foreground)]">
						{slot.label}
					</h3>
					<ModelSelector
						catalog={catalogs[slot.operation]}
						bind:provider={selectedProvider[slot.key]}
						bind:model={selectedModel[slot.key]}
						disabled={saving || catalogs[slot.operation].length === 0}
						idPrefix={`model-${slot.key}`}
						providerLabel="Provider"
						modelLabel="Model"
					/>
					<p class="font-mono mt-1 text-xs text-[var(--color-muted-foreground)]">
						{slot.description}
					</p>
				</div>
			{/each}
		</div>

		{#if error}
			<div class="mt-4 border-2 border-[var(--color-error)] bg-red-50 px-3 py-2 font-mono text-xs text-[var(--color-error)]">
				{error}
			</div>
		{/if}

		<div class="mt-4 flex items-center gap-3">
			<button
				onclick={handleSave}
				disabled={saving}
				class="border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-4 py-2 font-mono text-xs uppercase tracking-wider text-[var(--color-primary-foreground)] shadow-brutal transition-all duration-200 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
			>
				{saving ? 'Saving…' : 'Save Model Preferences'}
			</button>
			{#if saved}
				<span class="font-mono text-xs text-[var(--color-success)]">Saved</span>
			{/if}
		</div>
	{/if}
</section>
