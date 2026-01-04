import type { AppState, WorkflowStep } from '$lib/types';

let state = $state<AppState>({
	jobDescription: '',
	status: 'idle',
	currentStep: 'queued',
	jobId: null,
	pdfBlob: null,
	autoDownloadFailed: false,
	errorMessage: null,
	pollingInterval: null
});

export const appState = {
	get value() {
		return state;
	},

	setJobDescription(description: string) {
		state.jobDescription = description;
	},

	setSubmitting() {
		state.status = 'submitting';
		state.errorMessage = null;
	},

	startPolling(jobId: string) {
		state.status = 'polling';
		state.jobId = jobId;
		state.currentStep = 'queued';
	},

	updateStep(step: WorkflowStep) {
		state.currentStep = step;
	},

	setCompleted(pdfBlob: Blob, autoDownloadSucceeded: boolean) {
		state.status = 'completed';
		state.currentStep = 'completed';
		state.pdfBlob = pdfBlob;
		state.autoDownloadFailed = !autoDownloadSucceeded;
	},

	setError(message: string) {
		state.status = 'failed';
		// Don't change currentStep - preserve the step where error occurred
		state.errorMessage = message;
	},

	setPollingInterval(intervalId: number) {
		state.pollingInterval = intervalId;
	},

	// Clean up polling interval
	cleanup() {
		if (state.pollingInterval) {
			clearInterval(state.pollingInterval);
			state.pollingInterval = null;
		}
	},

	reset() {
		this.cleanup(); // Clear interval before resetting
		// Update each property individually to trigger reactivity
		state.jobDescription = '';
		state.status = 'idle';
		state.currentStep = 'queued';
		state.jobId = null;
		state.pdfBlob = null;
		state.autoDownloadFailed = false;
		state.errorMessage = null;
		state.pollingInterval = null;
	}
};
