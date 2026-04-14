<script lang="ts">
	import type { UserFilterPreferences } from '$lib/types/index';
	import { updateFilterPreferences, generateFilterPrompt } from '$lib/api/settings';
	import { auth } from '$lib/stores/auth.svelte';

	let { prefs: initialPrefs }: { prefs: UserFilterPreferences } = $props();

	let enabled = $state(initialPrefs.enabled);
	let naturalLanguagePrefs = $state(initialPrefs.natural_language_prefs);
	let customPrompt = $state(initialPrefs.custom_prompt ?? '');
	let rejectThreshold = $state(initialPrefs.reject_threshold);
	let warningThreshold = $state(initialPrefs.warning_threshold);

	let saving = $state(false);
	let saved = $state(false);
	let generating = $state(false);
	let error = $state<string | null>(null);

	async function handleGeneratePrompt() {
		if (!naturalLanguagePrefs.trim()) return;
		generating = true;
		error = null;
		try {
			const result = await generateFilterPrompt(naturalLanguagePrefs);
			customPrompt = result.prompt;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to generate prompt';
		} finally {
			generating = false;
		}
	}

	async function handleSave() {
		if (warningThreshold < rejectThreshold) {
			error = 'Warning threshold must be greater than or equal to reject threshold';
			return;
		}

		saving = true;
		error = null;
		saved = false;

		const payload: UserFilterPreferences = {
			enabled,
			natural_language_prefs: naturalLanguagePrefs,
			custom_prompt: customPrompt.trim() || null,
			reject_threshold: rejectThreshold,
			warning_threshold: warningThreshold,
		};

		try {
			const updated = await updateFilterPreferences(payload);
			auth.setUser(updated);
			saved = true;
			setTimeout(() => (saved = false), 2000);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to save filter preferences';
		} finally {
			saving = false;
		}
	}
</script>

<section class="border-4 border-[var(--color-foreground)] bg-white p-6 shadow-brutal">
	<div class="mb-4 flex items-center justify-between">
		<h2 class="font-heading text-lg tracking-tight">Job Filter</h2>
		<label class="flex cursor-pointer items-center gap-2">
			<span class="font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
				{enabled ? 'Enabled' : 'Disabled'}
			</span>
			<div
				class="relative h-6 w-10 border-2 border-[var(--color-foreground)] transition-colors {enabled ? 'bg-[var(--color-primary)]' : 'bg-white'}"
			>
				<div
					class="absolute top-0.5 h-4 w-4 border border-[var(--color-foreground)] bg-white transition-all {enabled ? 'left-4' : 'left-0.5'}"
				></div>
			</div>
			<input type="checkbox" bind:checked={enabled} class="sr-only" />
		</label>
	</div>

	<p class="font-mono mb-4 text-xs text-[var(--color-muted-foreground)]">
		LLM evaluates each LinkedIn job posting for hidden disqualifiers and scores suitability 0-100.
		Jobs below the reject threshold are filtered out before CV generation.
	</p>

	<div class="mb-4">
		<label
			for="naturalLanguagePrefs"
			class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
		>
			Your Preferences
		</label>
		<textarea
			id="naturalLanguagePrefs"
			bind:value={naturalLanguagePrefs}
			rows={4}
			disabled={saving || !enabled}
			placeholder="Describe what you don't want in plain language. e.g. I don't want jobs that require security clearance, on-site presence, or less than 3 years experience. I'm looking for remote senior backend roles with Python or Go."
			class="font-body w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 text-sm text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50"
		></textarea>
	</div>

	<div class="mb-4 flex items-center gap-3">
		<button
			onclick={handleGeneratePrompt}
			disabled={generating || saving || !enabled || !naturalLanguagePrefs.trim()}
			class="inline-flex items-center gap-2 border-2 border-[var(--color-foreground)] bg-[var(--color-foreground)] px-4 py-2 font-mono text-xs uppercase tracking-wider text-white shadow-brutal transition-all duration-200 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
		>
			{#if generating}
				<span class="inline-block h-3.5 w-3.5 animate-spin border-2 border-white"></span>
				Generating…
			{:else}
				Generate Prompt
			{/if}
		</button>
		{#if !generating}
			<span class="font-mono text-xs text-[var(--color-muted-foreground)]">
				Uses LLM to convert your preferences into a structured filter prompt
			</span>
		{:else}
			<span class="font-mono text-xs text-[var(--color-muted-foreground)]">
				This may take 15–30 seconds
			</span>
		{/if}
	</div>

	<div class="mb-4">
		<label
			for="customPrompt"
			class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
		>
			Filter Prompt
		</label>
		<textarea
			id="customPrompt"
			bind:value={customPrompt}
			rows={8}
			disabled={saving || !enabled}
			placeholder="The generated filter prompt will appear here. You can edit it freely. Leave blank to use the built-in default prompt."
			class="font-body w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 font-mono text-xs text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50"
		></textarea>
		<p class="font-mono mt-1 text-xs text-[var(--color-muted-foreground)]">
			This prompt is sent to the LLM for each job. Leave empty to use the default template.
		</p>
	</div>

	<div class="mb-4 grid gap-4 sm:grid-cols-2">
		<div>
			<label
				for="rejectThreshold"
				class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
			>
				Reject Threshold
			</label>
			<input
				id="rejectThreshold"
				type="number"
				bind:value={rejectThreshold}
				min="0"
				max="100"
				disabled={saving || !enabled}
				class="font-body w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 text-sm text-[var(--color-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50"
			/>
			<p class="font-mono mt-1 text-xs text-[var(--color-muted-foreground)]">
				Jobs scoring below this are filtered out entirely (default: 30)
			</p>
		</div>

		<div>
			<label
				for="warningThreshold"
				class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
			>
				Warning Threshold
			</label>
			<input
				id="warningThreshold"
				type="number"
				bind:value={warningThreshold}
				min="0"
				max="100"
				disabled={saving || !enabled}
				class="font-body w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 text-sm text-[var(--color-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50"
			/>
			<p class="font-mono mt-1 text-xs text-[var(--color-muted-foreground)]">
				Jobs scoring below this show a warning badge in review (default: 70)
			</p>
		</div>
	</div>

	{#if error}
		<div class="mb-4 border-2 border-[var(--color-error)] bg-red-50 px-3 py-2 font-mono text-xs text-[var(--color-error)]">
			{error}
		</div>
	{/if}

	<div class="flex items-center gap-3">
		<button
			onclick={handleSave}
			disabled={saving}
			class="border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-4 py-2 font-mono text-xs uppercase tracking-wider text-[var(--color-primary-foreground)] shadow-brutal transition-all duration-200 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
		>
			{saving ? 'Saving...' : 'Save Filter Preferences'}
		</button>
		{#if saved}
			<span class="font-mono text-xs text-[var(--color-success)]">Saved</span>
		{/if}
	</div>
</section>
