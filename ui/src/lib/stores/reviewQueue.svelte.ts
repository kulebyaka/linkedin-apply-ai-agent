import type { PendingApproval, Decision, DecisionResponse } from '$lib/types';
import { fetchPendingApprovals, submitDecision as apiSubmitDecision } from '$lib/api/hitl';

// Reactive state
let pendingJobs = $state<PendingApproval[]>([]);
let currentIndex = $state(0);
let isLoading = $state(false);
let isSubmitting = $state(false);
let error = $state<string | null>(null);

// Derived values
const currentJob = $derived(pendingJobs[currentIndex] ?? null);
const hasNext = $derived(currentIndex < pendingJobs.length - 1);
const hasPrevious = $derived(currentIndex > 0);
const totalCount = $derived(pendingJobs.length);

// Actions
function goToNext(): void {
	if (currentIndex < pendingJobs.length - 1) {
		currentIndex++;
	}
}

function goToPrevious(): void {
	if (currentIndex > 0) {
		currentIndex--;
	}
}

async function loadPending(): Promise<void> {
	isLoading = true;
	error = null;
	try {
		pendingJobs = await fetchPendingApprovals();
		currentIndex = 0;
	} catch (e) {
		error = e instanceof Error ? e.message : 'Failed to load pending approvals';
	} finally {
		isLoading = false;
	}
}

async function submitDecision(
	decision: Decision,
	feedback?: string
): Promise<DecisionResponse | null> {
	const job = pendingJobs[currentIndex];
	if (!job) return null;

	isSubmitting = true;
	error = null;
	try {
		const result = await apiSubmitDecision(job.job_id, decision, feedback);

		// Remove job from queue
		const jobId = job.job_id;
		pendingJobs = pendingJobs.filter((j) => j.job_id !== jobId);

		// Adjust index if needed
		if (currentIndex >= pendingJobs.length && currentIndex > 0) {
			currentIndex--;
		}

		return result;
	} catch (e) {
		error = e instanceof Error ? e.message : 'Failed to submit decision';
		return null;
	} finally {
		isSubmitting = false;
	}
}

function clearError(): void {
	error = null;
}

// Export as object with getters for reactivity
export const reviewQueue = {
	get currentJob() {
		return currentJob;
	},
	get currentIndex() {
		return currentIndex;
	},
	get totalCount() {
		return totalCount;
	},
	get hasNext() {
		return hasNext;
	},
	get hasPrevious() {
		return hasPrevious;
	},
	get isLoading() {
		return isLoading;
	},
	get isSubmitting() {
		return isSubmitting;
	},
	get error() {
		return error;
	},

	goToNext,
	goToPrevious,
	loadPending,
	submitDecision,
	clearError,
};
