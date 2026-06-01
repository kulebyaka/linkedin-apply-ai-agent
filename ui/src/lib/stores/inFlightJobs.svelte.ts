import type { PendingApproval } from '$lib/types';
import { fetchPendingApprovals } from '$lib/api/hitl';

let inFlightJobs = $state<PendingApproval[]>([]);
let isLoading = $state(false);
let error = $state<string | null>(null);
let pollHandle: ReturnType<typeof setInterval> | null = null;

const POLL_INTERVAL_MS = 3000;

async function load(): Promise<void> {
	try {
		inFlightJobs = await fetchPendingApprovals(['queued', 'processing', 'retrying']);
	} catch (e) {
		error = e instanceof Error ? e.message : 'Failed to load in-flight jobs';
	}
}

async function loadInitial(): Promise<void> {
	isLoading = true;
	error = null;
	try {
		await load();
	} finally {
		isLoading = false;
	}
}

function startPolling(): void {
	if (pollHandle !== null) return;
	pollHandle = setInterval(() => {
		void load();
	}, POLL_INTERVAL_MS);
}

function stopPolling(): void {
	if (pollHandle !== null) {
		clearInterval(pollHandle);
		pollHandle = null;
	}
}

export const inFlightStore = {
	get jobs() {
		return inFlightJobs;
	},
	get isLoading() {
		return isLoading;
	},
	get error() {
		return error;
	},
	loadInitial,
	startPolling,
	stopPolling,
};
