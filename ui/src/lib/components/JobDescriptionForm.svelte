<script lang="ts">
	import { onMount } from 'svelte';
	import { validateJobDescription } from '$lib/utils/validation';
	import { getModelCatalog } from '$lib/api/settings';
	import type { ModelCatalogEntry } from '$lib/api/auth';
	import type { TemplateName, LLMProvider, LLMModel } from '$lib/types';
	import ModelSelector from '$lib/components/ModelSelector.svelte';

	interface Props {
		onSubmit: (description: string, templateName: TemplateName, llmProvider?: LLMProvider, llmModel?: LLMModel) => void;
		isLoading: boolean;
		errorMessage?: string;
		initialValue?: string;
		initialTemplate?: TemplateName;
		initialLLMProvider?: LLMProvider;
		initialLLMModel?: LLMModel;
	}

	let { onSubmit, isLoading, errorMessage, initialValue = '', initialTemplate = 'compact', initialLLMProvider, initialLLMModel }: Props = $props();

	// svelte-ignore state_referenced_locally -- intentional: local state seeded from prop defaults
	let jobDescription = $state(initialValue);
	// svelte-ignore state_referenced_locally
	let selectedTemplate = $state<TemplateName>(initialTemplate);
	let selectedLLMProvider = $state<string>('');
	let selectedLLMModel = $state<string>('');
	let validationError = $state<string | null>(null);

	// Model catalog for CV generation, fetched from the API and filtered to
	// providers with an API key configured on the server.
	let catalog = $state<ModelCatalogEntry[]>([]);
	let modelsLoading = $state(true);
	let modelsError = $state<string | null>(null);

	const templates: { value: TemplateName; label: string; description: string }[] = [
		{ value: 'compact', label: 'Compact', description: '2-column layout, space-efficient' },
		{ value: 'modern', label: 'Modern', description: 'Clean single-column design' },
		{ value: 'profile-card', label: 'Profile Card', description: 'LinkedIn-style layout' },
	];

	function inCatalog(entries: ModelCatalogEntry[], provider: string, model: string): boolean {
		return entries.some((e) => e.provider === provider && e.model === model);
	}

	onMount(async () => {
		try {
			const { models, default: dflt } = await getModelCatalog('cv_generation');
			catalog = models;

			// Seed from explicit props if valid, else the server default, else
			// the first catalog entry so the dropdowns are never empty.
			if (initialLLMProvider && initialLLMModel && inCatalog(models, initialLLMProvider, initialLLMModel)) {
				selectedLLMProvider = initialLLMProvider;
				selectedLLMModel = initialLLMModel;
			} else if (inCatalog(models, dflt.provider, dflt.model)) {
				selectedLLMProvider = dflt.provider;
				selectedLLMModel = dflt.model;
			} else if (models.length > 0) {
				selectedLLMProvider = models[0].provider;
				selectedLLMModel = models[0].model;
			}
		} catch (err) {
			modelsError = err instanceof Error ? err.message : 'Could not load model catalog';
		} finally {
			modelsLoading = false;
		}
	});

	function handleSubmit(e: Event) {
		e.preventDefault();

		const error = validateJobDescription(jobDescription);
		if (error) {
			validationError = error;
			return;
		}

		validationError = null;
		onSubmit(
			jobDescription,
			selectedTemplate,
			(selectedLLMProvider || undefined) as LLMProvider | undefined,
			(selectedLLMModel || undefined) as LLMModel | undefined,
		);
	}

	function handleInput() {
		// Clear validation error when user starts typing
		if (validationError) {
			validationError = null;
		}
	}

	function handleFocus(e: FocusEvent) {
		const target = e.currentTarget as HTMLTextAreaElement;
		target.style.borderColor = 'var(--color-primary)';
		target.style.boxShadow = '6px 6px 0 var(--color-foreground)';
	}

	function handleBlur(e: FocusEvent) {
		const target = e.currentTarget as HTMLTextAreaElement;
		target.style.borderColor = 'var(--color-foreground)';
		target.style.boxShadow = '4px 4px 0 var(--color-foreground)';
	}

	// Compute if submit button should be disabled
	const isSubmitDisabled = $derived(isLoading || jobDescription.trim().length < 50);
</script>

<form onsubmit={handleSubmit} class="w-full max-w-4xl mx-auto">
	<div class="space-y-6">
		<div>
			<label for="job-description" class="block font-mono text-sm font-medium tracking-wide text-[var(--color-foreground)] mb-3">
				Job Description
			</label>
			<textarea
				id="job-description"
				bind:value={jobDescription}
				oninput={handleInput}
				onfocus={handleFocus}
				onblur={handleBlur}
				disabled={isLoading}
				rows="15"
				placeholder="Paste the full job description here (minimum 50 characters)...

Example:
Software Engineer - Full Stack
ABC Company

We are looking for a talented Full Stack Developer with experience in React, Node.js, and PostgreSQL...

Requirements:
- 3+ years of experience
- Strong knowledge of JavaScript/TypeScript
..."
				class="w-full px-6 py-4 border-2 border-[var(--color-foreground)] bg-white text-[var(--color-foreground)] shadow-brutal transition-all duration-200 resize-y min-h-[300px] max-h-[600px] disabled:cursor-not-allowed font-mono text-sm leading-relaxed"
			></textarea>

			<!-- Character count -->
			{#if jobDescription.trim().length < 50}
				<div class="mt-3 font-mono text-xs tracking-wider text-[var(--color-muted-foreground)]">
					50 characters minimum
				</div>
			{/if}

			<!-- Validation error -->
			{#if validationError}
				<p class="mt-3 text-sm font-medium text-[var(--color-destructive)]">{validationError}</p>
			{/if}

			<!-- Server error -->
			{#if errorMessage}
				<p class="mt-3 text-sm font-medium text-[var(--color-destructive)]">{errorMessage}</p>
			{/if}
		</div>

		<!-- Template selector -->
		<div>
			<label for="template-select" class="block font-mono text-sm font-medium tracking-wide text-[var(--color-foreground)] mb-3">
				CV Template
			</label>
			<div class="relative">
				<select
					id="template-select"
					bind:value={selectedTemplate}
					disabled={isLoading}
					class="w-full px-6 py-4 pr-12 border-2 border-[var(--color-foreground)] bg-white text-[var(--color-foreground)] shadow-brutal transition-all duration-200 disabled:cursor-not-allowed font-mono text-sm cursor-pointer appearance-none"
					onfocus={(e) => { e.currentTarget.style.borderColor = 'var(--color-primary)'; e.currentTarget.style.boxShadow = '6px 6px 0 var(--color-foreground)'; }}
					onblur={(e) => { e.currentTarget.style.borderColor = 'var(--color-foreground)'; e.currentTarget.style.boxShadow = '4px 4px 0 var(--color-foreground)'; }}
				>
					{#each templates as template}
						<option value={template.value}>{template.label} - {template.description}</option>
					{/each}
				</select>
				<div class="pointer-events-none absolute inset-y-0 right-0 flex items-center px-4 text-[var(--color-foreground)]">
					<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
					</svg>
				</div>
			</div>
		</div>

		<!-- LLM Provider & Model selectors -->
		{#if modelsError}
			<p class="font-mono text-sm font-medium text-[var(--color-destructive)]">{modelsError}</p>
		{:else if modelsLoading}
			<p class="font-mono text-sm text-[var(--color-muted-foreground)]">Loading models…</p>
		{:else}
			<ModelSelector
				{catalog}
				bind:provider={selectedLLMProvider}
				bind:model={selectedLLMModel}
				disabled={isLoading}
				idPrefix="llm"
			/>
		{/if}

		<button
			type="submit"
			disabled={isSubmitDisabled}
			class="w-full border-2 py-4 px-8 font-mono text-sm uppercase tracking-wider transition-all duration-200 disabled:cursor-not-allowed disabled:pointer-events-none {isSubmitDisabled
				? 'border-[var(--color-muted)] bg-[var(--color-muted)] text-[var(--color-muted-foreground)] opacity-50'
				: 'border-[var(--color-primary)] bg-[var(--color-primary)] text-[var(--color-primary-foreground)] shadow-brutal hover:-translate-y-0.5'}"
		>
			{#if isLoading}
				<span class="flex items-center justify-center">
					<svg
						class="animate-spin -ml-1 mr-3 h-5 w-5 text-[var(--color-primary-foreground)]"
						xmlns="http://www.w3.org/2000/svg"
						fill="none"
						viewBox="0 0 24 24"
					>
						<circle
							class="opacity-25"
							cx="12"
							cy="12"
							r="10"
							stroke="currentColor"
							stroke-width="4"
						></circle>
						<path
							class="opacity-75"
							fill="currentColor"
							d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
						></path>
					</svg>
					Generating CV...
				</span>
			{:else}
				Generate CV
			{/if}
		</button>
	</div>
</form>
