import type { PendingApproval, Decision, DecisionResponse } from '$lib/types';
import {
	fetchPendingApprovals,
	fetchJobStats,
	submitDecision as apiSubmitDecision,
	deleteJob as apiDeleteJob,
	type JobStatusCounts,
} from '$lib/api/hitl';

// Reactive state
let pendingJobs = $state<PendingApproval[]>([]);
let currentIndex = $state(0);
let isLoading = $state(false);
let isSubmitting = $state(false);
let error = $state<string | null>(null);
let statusCounts = $state<JobStatusCounts>({});

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

function selectJob(jobId: string): boolean {
	const index = pendingJobs.findIndex((j) => j.job_id === jobId);
	if (index !== -1) {
		currentIndex = index;
		return true;
	}
	return false;
}

async function loadPending(): Promise<void> {
	isLoading = true;
	error = null;
	try {
		const [pending, counts] = await Promise.all([
			fetchPendingApprovals(),
			fetchJobStats().catch(() => ({}) as JobStatusCounts),
		]);
		pendingJobs = pending;
		statusCounts = counts;
		currentIndex = 0;
	} catch (e) {
		error = e instanceof Error ? e.message : 'Failed to load pending approvals';
	} finally {
		isLoading = false;
	}
}

async function refreshStats(): Promise<void> {
	try {
		statusCounts = await fetchJobStats();
	} catch {
		// Stats are auxiliary — don't surface errors
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

		void refreshStats();
		return result;
	} catch (e) {
		error = e instanceof Error ? e.message : 'Failed to submit decision';
		return null;
	} finally {
		isSubmitting = false;
	}
}

async function deleteCurrent(): Promise<boolean> {
	const job = pendingJobs[currentIndex];
	if (!job) return false;

	isSubmitting = true;
	error = null;
	try {
		await apiDeleteJob(job.job_id);

		const jobId = job.job_id;
		pendingJobs = pendingJobs.filter((j) => j.job_id !== jobId);
		if (currentIndex >= pendingJobs.length && currentIndex > 0) {
			currentIndex--;
		}
		void refreshStats();
		return true;
	} catch (e) {
		error = e instanceof Error ? e.message : 'Failed to delete job';
		return false;
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
	get statusCounts() {
		return statusCounts;
	},

	goToNext,
	goToPrevious,
	selectJob,
	loadPending,
	refreshStats,
	submitDecision,
	deleteCurrent,
	clearError,
};
