<script lang="ts">
	import { validateJobDescription } from '$lib/utils/validation';
	import type { TemplateName, LLMProvider, LLMModel } from '$lib/types';

	interface Props {
		onSubmit: (description: string, templateName: TemplateName, llmProvider?: LLMProvider, llmModel?: LLMModel) => void;
		isLoading: boolean;
		errorMessage?: string;
		initialValue?: string;
		initialTemplate?: TemplateName;
		initialLLMProvider?: LLMProvider;
		initialLLMModel?: LLMModel;
	}

	let { onSubmit, isLoading, errorMessage, initialValue = '', initialTemplate = 'compact', initialLLMProvider = 'openai', initialLLMModel = 'gpt-4o-mini' }: Props = $props();

	let jobDescription = $state(initialValue);
	let selectedTemplate = $state<TemplateName>(initialTemplate);
	let selectedLLMProvider = $state<LLMProvider>(initialLLMProvider);
	let selectedLLMModel = $state<LLMModel>(initialLLMModel);
	let validationError = $state<string | null>(null);

	const templates: { value: TemplateName; label: string; description: string }[] = [
		{ value: 'compact', label: 'Compact', description: '2-column layout, space-efficient' },
		{ value: 'modern', label: 'Modern', description: 'Clean single-column design' },
		{ value: 'profile-card', label: 'Profile Card', description: 'LinkedIn-style layout' },
	];

	const llmProviders: { value: LLMProvider; label: string }[] = [
		{ value: 'openai', label: 'OpenAI' },
		{ value: 'anthropic', label: 'Anthropic' },
	];

	const modelsByProvider: Record<LLMProvider, { value: LLMModel; label: string; description: string }[]> = {
		openai: [
			{ value: 'gpt-5-mini', label: 'GPT-5 Mini', description: 'Latest & smartest' },
			{ value: 'gpt-4o-mini', label: 'GPT-4o Mini', description: 'Fast & reliable' },
			{ value: 'gpt-4o', label: 'GPT-4o', description: 'Best quality' },
			{ value: 'gpt-3.5-turbo', label: 'GPT-3.5 Turbo', description: 'Fastest (no strict schema)' },
		],
		anthropic: [
			{ value: 'claude-haiku-4.5', label: 'Claude Haiku 4.5', description: 'Fast & efficient' },
		],
	};

	// Get available models for selected provider
	const availableModels = $derived(modelsByProvider[selectedLLMProvider]);

	// When provider changes, reset model to first available
	$effect(() => {
		const models = modelsByProvider[selectedLLMProvider];
		if (models && !models.some(m => m.value === selectedLLMModel)) {
			selectedLLMModel = models[0].value;
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
		onSubmit(jobDescription, selectedTemplate, selectedLLMProvider, selectedLLMModel);
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
			<div class="mt-3 font-mono text-xs tracking-wider text-[var(--color-muted-foreground)]">
				{jobDescription.length} / 50 characters minimum
			</div>

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
		<div class="grid grid-cols-2 gap-4">
			<div>
				<label for="llm-provider-select" class="block font-mono text-sm font-medium tracking-wide text-[var(--color-foreground)] mb-3">
					LLM Provider
				</label>
				<div class="relative">
					<select
						id="llm-provider-select"
						bind:value={selectedLLMProvider}
						disabled={isLoading}
						class="w-full px-6 py-4 pr-12 border-2 border-[var(--color-foreground)] bg-white text-[var(--color-foreground)] shadow-brutal transition-all duration-200 disabled:cursor-not-allowed font-mono text-sm cursor-pointer appearance-none"
						onfocus={(e) => { e.currentTarget.style.borderColor = 'var(--color-primary)'; e.currentTarget.style.boxShadow = '6px 6px 0 var(--color-foreground)'; }}
						onblur={(e) => { e.currentTarget.style.borderColor = 'var(--color-foreground)'; e.currentTarget.style.boxShadow = '4px 4px 0 var(--color-foreground)'; }}
					>
						{#each llmProviders as provider}
							<option value={provider.value}>{provider.label}</option>
						{/each}
					</select>
					<div class="pointer-events-none absolute inset-y-0 right-0 flex items-center px-4 text-[var(--color-foreground)]">
						<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
						</svg>
					</div>
				</div>
			</div>

			<div>
				<label for="llm-model-select" class="block font-mono text-sm font-medium tracking-wide text-[var(--color-foreground)] mb-3">
					Model
				</label>
				<div class="relative">
					<select
						id="llm-model-select"
						bind:value={selectedLLMModel}
						disabled={isLoading}
						class="w-full px-6 py-4 pr-12 border-2 border-[var(--color-foreground)] bg-white text-[var(--color-foreground)] shadow-brutal transition-all duration-200 disabled:cursor-not-allowed font-mono text-sm cursor-pointer appearance-none"
						onfocus={(e) => { e.currentTarget.style.borderColor = 'var(--color-primary)'; e.currentTarget.style.boxShadow = '6px 6px 0 var(--color-foreground)'; }}
						onblur={(e) => { e.currentTarget.style.borderColor = 'var(--color-foreground)'; e.currentTarget.style.boxShadow = '4px 4px 0 var(--color-foreground)'; }}
					>
						{#each availableModels as model}
							<option value={model.value}>{model.label} - {model.description}</option>
						{/each}
					</select>
					<div class="pointer-events-none absolute inset-y-0 right-0 flex items-center px-4 text-[var(--color-foreground)]">
						<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
						</svg>
					</div>
				</div>
			</div>
		</div>

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
