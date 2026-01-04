<script lang="ts">
	import type { WorkflowStep } from '$lib/types';

	interface Props {
		currentStep: WorkflowStep;
		hasError?: boolean;
	}

	let { currentStep, hasError = false }: Props = $props();

	interface Step {
		id: WorkflowStep;
		label: string;
		description: string;
	}

	const steps: Step[] = [
		{
			id: 'queued',
			label: 'Queued',
			description: 'Job submitted successfully'
		},
		{
			id: 'extracting',
			label: 'Extracting',
			description: 'Extracting job details from description'
		},
		{
			id: 'composing_cv',
			label: 'Composing CV',
			description: 'Tailoring CV to job requirements'
		},
		{
			id: 'generating_pdf',
			label: 'Generating PDF',
			description: 'Creating professional PDF resume'
		},
		{
			id: 'completed',
			label: 'Complete',
			description: 'Your CV is ready!'
		}
	];

	// Map intermediate workflow states to display steps
	function normalizeStep(step: WorkflowStep): WorkflowStep {
		const stepMap: Record<string, WorkflowStep> = {
			'job_extracted': 'composing_cv',  // After extraction, move to CV composition
			'cv_composed': 'generating_pdf',   // After CV composition, move to PDF generation
			'pdf_generated': 'completed',      // After PDF generation, show as completed
			'saving': 'completed'              // Saving is the final step before completed
		};
		return stepMap[step] || step;
	}

	// Compute current step index with normalized step
	const normalizedStep = $derived(normalizeStep(currentStep));
	const currentStepIndex = $derived(steps.findIndex((s) => s.id === normalizedStep));

	function isStepCompleted(stepIndex: number): boolean {
		return stepIndex < currentStepIndex;
	}

	function isStepActive(stepIndex: number): boolean {
		return stepIndex === currentStepIndex;
	}

	function isStepFailed(stepIndex: number): boolean {
		return hasError && stepIndex === currentStepIndex;
	}
</script>

<div class="w-full max-w-4xl mx-auto py-12">
	<div class="relative">
		<!-- Progress bar background -->
		<div class="absolute top-6 left-0 w-full h-0.5" style="background-color: var(--color-warm-gray-light);"></div>

		<!-- Progress bar fill -->
		<div
			class="absolute top-6 left-0 h-0.5 transition-all duration-700 ease-out"
			style="width: {currentStepIndex === -1 ? 0 : (currentStepIndex / (steps.length - 1)) * 100}%; background-color: var(--color-amber);"
		></div>

		<!-- Steps -->
		<div class="relative flex justify-between">
			{#each steps as step, index}
				<div class="flex flex-col items-center" style="flex: 1;">
					<!-- Step indicator -->
					<div
						class="w-12 h-12 border-2 flex items-center justify-center transition-all duration-300"
						style="{isStepFailed(index)
							? 'background-color: #fef2f2; border-color: var(--color-error);'
							: isStepCompleted(index)
								? 'background-color: var(--color-amber); border-color: var(--color-amber);'
								: isStepActive(index)
									? 'background-color: white; border-color: var(--color-amber);'
									: 'background-color: white; border-color: var(--color-warm-gray-light);'}"
					>
						{#if isStepFailed(index)}
							<!-- Error X icon -->
							<svg
								class="w-7 h-7"
								style="color: var(--color-error);"
								fill="none"
								stroke="currentColor"
								viewBox="0 0 24 24"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2.5"
									d="M6 18L18 6M6 6l12 12"
								></path>
							</svg>
						{:else if isStepCompleted(index)}
							<!-- Checkmark -->
							<svg
								class="w-7 h-7"
								style="color: var(--color-charcoal);"
								fill="none"
								stroke="currentColor"
								viewBox="0 0 24 24"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2.5"
									d="M5 13l4 4L19 7"
								></path>
							</svg>
						{:else if isStepActive(index)}
							<!-- Animated pulse square -->
							<div class="relative">
								<div class="w-4 h-4 animate-pulse" style="background-color: var(--color-amber);"></div>
							</div>
						{:else}
							<!-- Empty square -->
							<div class="w-3 h-3" style="background-color: var(--color-warm-gray-light);"></div>
						{/if}
					</div>

					<!-- Step label -->
					<div class="mt-4 text-center max-w-[140px]">
						<div
							class="text-sm font-mono tracking-wide font-medium uppercase"
							style="{isStepFailed(index)
								? 'color: var(--color-error);'
								: isStepActive(index)
									? 'color: var(--color-amber);'
									: isStepCompleted(index)
										? 'color: var(--color-charcoal);'
										: 'color: var(--color-warm-gray);'}"
						>
							{step.label}
						</div>
						<div class="text-xs mt-1.5 leading-snug" style="color: var(--color-warm-gray);">
							{isStepFailed(index) ? 'Error occurred - check details below' : step.description}
						</div>
					</div>
				</div>
			{/each}
		</div>
	</div>
</div>
