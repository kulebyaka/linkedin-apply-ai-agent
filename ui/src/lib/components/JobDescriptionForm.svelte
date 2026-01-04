<script lang="ts">
	import { validateJobDescription } from '$lib/utils/validation';

	interface Props {
		onSubmit: (description: string) => void;
		isLoading: boolean;
		errorMessage?: string;
		initialValue?: string;
	}

	let { onSubmit, isLoading, errorMessage, initialValue = '' }: Props = $props();

	let jobDescription = $state(initialValue);
	let validationError = $state<string | null>(null);

	function handleSubmit(e: Event) {
		e.preventDefault();

		const error = validateJobDescription(jobDescription);
		if (error) {
			validationError = error;
			return;
		}

		validationError = null;
		onSubmit(jobDescription);
	}

	function handleInput() {
		// Clear validation error when user starts typing
		if (validationError) {
			validationError = null;
		}
	}

	function handleFocus(e: FocusEvent) {
		const target = e.currentTarget as HTMLTextAreaElement;
		target.style.borderColor = 'var(--color-amber)';
		target.style.boxShadow = '4px 4px 0 var(--color-amber-dark)';
	}

	function handleBlur(e: FocusEvent) {
		const target = e.currentTarget as HTMLTextAreaElement;
		target.style.borderColor = 'var(--color-warm-gray-light)';
		target.style.boxShadow = '2px 2px 0 var(--color-warm-gray-light)';
	}

	// Compute if submit button should be disabled
	const isSubmitDisabled = $derived(isLoading || jobDescription.trim().length < 50);
</script>

<form onsubmit={handleSubmit} class="w-full max-w-4xl mx-auto">
	<div class="space-y-6">
		<div>
			<label for="job-description" class="block text-sm font-medium tracking-wide mb-3" style="color: var(--color-charcoal); font-family: 'JetBrains Mono', monospace;">
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
				class="w-full px-6 py-4 border-2 transition-all duration-200 resize-y min-h-[300px] max-h-[600px] disabled:cursor-not-allowed font-mono text-sm leading-relaxed"
				style="border-color: var(--color-warm-gray-light); background-color: white; color: var(--color-charcoal); box-shadow: 2px 2px 0 var(--color-warm-gray-light);"
			></textarea>

			<!-- Character count -->
			<div class="mt-3 text-xs font-mono tracking-wider" style="color: var(--color-warm-gray);">
				{jobDescription.length} / 50 characters minimum
			</div>

			<!-- Validation error -->
			{#if validationError}
				<p class="mt-3 text-sm font-medium" style="color: var(--color-error);">{validationError}</p>
			{/if}

			<!-- Server error -->
			{#if errorMessage}
				<p class="mt-3 text-sm font-medium" style="color: var(--color-error);">{errorMessage}</p>
			{/if}
		</div>

		<button
			type="submit"
			disabled={isSubmitDisabled}
			class="w-full border-2 font-medium py-4 px-8 transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-50"
			style="{isSubmitDisabled ? 'border-color: var(--color-warm-gray-light); background-color: var(--color-warm-gray-light); color: var(--color-warm-gray);' : 'border-color: var(--color-amber); background-color: var(--color-amber); color: var(--color-charcoal); box-shadow: 4px 4px 0 var(--color-charcoal);'}"
			onmouseenter={isSubmitDisabled ? undefined : function(e) { e.currentTarget.style.transform = 'translateY(-2px)'; }}
			onmouseleave={isSubmitDisabled ? undefined : function(e) { e.currentTarget.style.transform = 'translateY(0)'; }}
		>
			{#if isLoading}
				<span class="flex items-center justify-center font-mono tracking-wide">
					<svg
						class="animate-spin -ml-1 mr-3 h-5 w-5"
						style="color: var(--color-charcoal);"
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
					GENERATING CV...
				</span>
			{:else}
				<span class="font-mono tracking-wide">GENERATE CV</span>
			{/if}
		</button>
	</div>
</form>
