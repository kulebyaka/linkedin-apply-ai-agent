<script lang="ts">
	import { onDestroy } from 'svelte';
	import { appState } from '$lib/stores/appState.svelte';
	import { submitJob, getJobStatus, downloadPDF, triggerDownload } from '$lib/api/client';
	import JobDescriptionForm from '$lib/components/JobDescriptionForm.svelte';
	import ProgressStepper from '$lib/components/ProgressStepper.svelte';
	import ToastNotification from '$lib/components/ToastNotification.svelte';

	// Reactive state references
	const currentState = appState.value;

	// Toast state
	let showToast = $state(false);
	let toastMessage = $state('');
	let toastType = $state<'error' | 'success' | 'info'>('info');

	// Handle form submission
	async function handleSubmit(description: string) {
		try {
			appState.setJobDescription(description);
			appState.setSubmitting();

			// Submit job
			const response = await submitJob(description);

			// Start polling
			appState.startPolling(response.job_id);
			startPolling(response.job_id);
		} catch (error) {
			const errorMsg = error instanceof Error ? error.message : 'Unknown error occurred';
			appState.setError(errorMsg);
			showErrorToast(`Failed to submit job: ${errorMsg}`);
		}
	}

	// Start polling for job status
	function startPolling(jobId: string) {
		const intervalId = setInterval(async () => {
			try {
				const status = await getJobStatus(jobId);

				// Update current step
				appState.updateStep(status.status);

				// Check if job completed
				if (status.status === 'completed') {
					clearInterval(intervalId);
					await handleJobCompleted(jobId, status);
				} else if (status.status === 'failed') {
					clearInterval(intervalId);
					const errorMsg = status.error_message || 'Job processing failed';
					appState.setError(errorMsg);
					showErrorToast(`CV generation failed: ${errorMsg}`);
				}
			} catch (error) {
				clearInterval(intervalId);
				const errorMsg = error instanceof Error ? error.message : 'Unknown error occurred';
				appState.setError(errorMsg);
				showErrorToast(`Failed to check job status: ${errorMsg}`);
			}
		}, 2000); // Poll every 2 seconds

		appState.setPollingInterval(intervalId);
	}

	// Handle job completion
	async function handleJobCompleted(jobId: string, status: any) {
		try {
			// Download PDF
			const pdfBlob = await downloadPDF(jobId);

			// Generate filename from job details
			const title = status.job_posting?.title || 'CV';
			const company = status.job_posting?.company || 'Job';
			const filename = `${company.replace(/[^a-z0-9]/gi, '_')}_${title.replace(/[^a-z0-9]/gi, '_')}_CV.pdf`;

			// Try auto-download
			const autoDownloadSucceeded = triggerDownload(pdfBlob, filename);

			// Update state
			appState.setCompleted(pdfBlob, autoDownloadSucceeded);

			// Show success toast
			if (autoDownloadSucceeded) {
				showSuccessToast('Your CV has been generated and downloaded!');
			} else {
				showInfoToast('Your CV is ready! Click the download button to save it.');
			}
		} catch (error) {
			const errorMsg = error instanceof Error ? error.message : 'Unknown error occurred';
			appState.setError(errorMsg);
			showErrorToast(`Failed to download PDF: ${errorMsg}`);
		}
	}

	// Manual download (fallback)
	function handleManualDownload() {
		if (!currentState.pdfBlob) return;

		const title = 'CV';
		const company = 'Job';
		const filename = `${company}_${title}_CV.pdf`;

		const succeeded = triggerDownload(currentState.pdfBlob, filename);
		if (succeeded) {
			showSuccessToast('CV downloaded successfully!');
		} else {
			showErrorToast('Failed to download PDF. Please try again.');
		}
	}

	// Reset and start over
	function handleReset() {
		appState.reset();
	}

	// Toast helpers
	function showErrorToast(message: string) {
		toastMessage = message;
		toastType = 'error';
		showToast = true;
	}

	function showSuccessToast(message: string) {
		toastMessage = message;
		toastType = 'success';
		showToast = true;
	}

	function showInfoToast(message: string) {
		toastMessage = message;
		toastType = 'info';
		showToast = true;
	}

	function closeToast() {
		showToast = false;
	}

	// Cleanup on component destroy
	onDestroy(() => {
		appState.cleanup();
	});
</script>

<svelte:head>
	<title>LinkedIn Job Application Agent - CV Generator</title>
</svelte:head>

<div class="min-h-screen grain py-16 px-4 sm:px-6 lg:px-8" style="background-color: var(--color-stone);">
	<div class="max-w-5xl mx-auto">
		<!-- Header -->
		<div class="mb-16">
			<div class="border-l-4 pl-6 mb-8" style="border-color: var(--color-amber);">
				<h1 class="text-5xl tracking-tight mb-3" style="color: var(--color-charcoal);">CV Generator</h1>
				<p class="text-lg font-light" style="color: var(--color-warm-gray);">
					Paste a job description and get a tailored CV PDF instantly
				</p>
			</div>
		</div>

		<!-- Main content -->
		<div class="space-y-8">
			<!-- Progress stepper (show when processing or failed) -->
			{#if currentState.status === 'polling' || currentState.status === 'submitting' || currentState.status === 'failed'}
				<ProgressStepper
					currentStep={currentState.currentStep}
					hasError={currentState.status === 'failed'}
				/>

				<div class="text-center mt-8">
					<p class="text-base font-mono tracking-wide" style="color: var(--color-warm-gray);">
						{#if currentState.status === 'failed'}
							<!-- Error message shown via toast -->
						{:else if currentState.currentStep === 'queued'}
							Job submitted successfully. Starting processing...
						{:else if currentState.currentStep === 'extracting'}
							Extracting job details from description...
						{:else if currentState.currentStep === 'composing_cv'}
							Tailoring your CV to match the job requirements...
						{:else if currentState.currentStep === 'generating_pdf'}
							Generating professional PDF resume...
						{/if}
					</p>
				</div>
			{/if}

			<!-- Form (show when idle or failed) -->
			{#if currentState.status === 'idle' || currentState.status === 'failed'}
				<JobDescriptionForm
					onSubmit={handleSubmit}
					isLoading={currentState.status === 'submitting'}
					errorMessage={currentState.status === 'failed' ? currentState.errorMessage : undefined}
					initialValue={currentState.jobDescription}
				/>
			{/if}

			<!-- Completed state -->
			{#if currentState.status === 'completed'}
				<div class="text-center space-y-8 py-12">
					<div class="inline-flex items-center justify-center w-20 h-20 rounded-none border-4" style="border-color: var(--color-success); background-color: transparent;">
						<svg class="w-12 h-12" style="color: var(--color-success);" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M5 13l4 4L19 7"
							></path>
						</svg>
					</div>

					<h2 class="text-3xl tracking-tight" style="color: var(--color-charcoal);">Your CV is Ready!</h2>

					{#if currentState.autoDownloadFailed && currentState.pdfBlob}
						<div class="space-y-6">
							<p class="text-base" style="color: var(--color-warm-gray);">
								Your browser blocked the automatic download. Click below to download manually.
							</p>
							<button
								onclick={handleManualDownload}
								class="inline-flex items-center px-8 py-4 border-2 text-base font-medium transition-all duration-200 hover:translate-y-[-2px]"
								style="border-color: var(--color-amber); background-color: var(--color-amber); color: var(--color-charcoal); box-shadow: 4px 4px 0 var(--color-charcoal);"
							>
								<svg
									class="w-5 h-5 mr-2"
									fill="none"
									stroke="currentColor"
									viewBox="0 0 24 24"
								>
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										stroke-width="2"
										d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
									></path>
								</svg>
								Download CV PDF
							</button>
						</div>
					{/if}

					<button
						onclick={handleReset}
						class="inline-flex items-center px-8 py-4 border-2 text-base font-medium transition-all duration-200 hover:translate-y-[-2px]"
						style="border-color: var(--color-charcoal); background-color: transparent; color: var(--color-charcoal); box-shadow: 4px 4px 0 var(--color-warm-gray-light);"
					>
						<svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M12 4v16m8-8H4"
							></path>
						</svg>
						Generate Another CV
					</button>
				</div>
			{/if}
		</div>
	</div>
</div>

<!-- Toast notification -->
{#if showToast}
	<ToastNotification message={toastMessage} type={toastType} onClose={closeToast} />
{/if}
