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

	let selected = $state<Record<keyof UserModelPreferences, string>>({
		cv_generation: '',
		job_filtering: '',
		filter_prompt_generation: '',
	});

	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let saving = $state(false);
	let saved = $state(false);
	let error = $state<string | null>(null);

	function choiceKey(c: ModelChoice | null | undefined): string {
		return c ? `${c.provider}::${c.model}` : '';
	}

	function entryKey(e: ModelCatalogEntry): string {
		return `${e.provider}::${e.model}`;
	}

	function resolveInitial(
		catalog: ModelCatalogEntry[],
		current: ModelChoice | null | undefined,
		fallback: ModelChoice,
	): string {
		if (current) {
			const key = choiceKey(current);
			if (catalog.some((e) => entryKey(e) === key)) return key;
		}
		// No stored preference — pre-select the global .env default if it's
		// in the catalog; otherwise the first entry (so dropdown is never empty).
		const defaultKey = choiceKey(fallback);
		if (catalog.some((e) => entryKey(e) === defaultKey)) return defaultKey;
		return catalog.length > 0 ? entryKey(catalog[0]) : '';
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
			selected = {
				cv_generation: resolveInitial(cv.models, prefs?.cv_generation, cv.default),
				job_filtering: resolveInitial(jf.models, prefs?.job_filtering, jf.default),
				filter_prompt_generation: resolveInitial(
					fp.models,
					prefs?.filter_prompt_generation,
					fp.default,
				),
			};
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Could not load model catalog';
		} finally {
			loading = false;
		}
	});

	function toChoice(key: string): ModelChoice | null {
		if (!key) return null;
		const [provider, model] = key.split('::');
		if (!provider || !model) return null;
		return { provider: provider as ModelChoice['provider'], model };
	}

	async function handleSave() {
		saving = true;
		error = null;
		saved = false;

		const payload: UserModelPreferences = {
			cv_generation: toChoice(selected.cv_generation),
			job_filtering: toChoice(selected.job_filtering),
			filter_prompt_generation: toChoice(selected.filter_prompt_generation),
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
					<label
						for={`model-${slot.key}`}
						class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
					>
						{slot.label}
					</label>
					<select
						id={`model-${slot.key}`}
						bind:value={selected[slot.key]}
						disabled={saving || catalogs[slot.operation].length === 0}
						class="font-mono w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 text-sm text-[var(--color-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50"
					>
						{#each catalogs[slot.operation] as entry (entryKey(entry))}
							<option value={entryKey(entry)}>{entry.label}</option>
						{/each}
					</select>
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
