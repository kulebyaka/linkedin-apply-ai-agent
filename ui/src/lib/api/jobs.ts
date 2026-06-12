import type { AdminJobRecord } from './admin';
import { handle } from './_http';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

export interface MyJobsFilters {
	status?: string[];
	source?: string[];
	created_from?: string;
	created_to?: string;
	search?: string;
	limit?: number;
	offset?: number;
}

export interface MyJobsResponse {
	items: AdminJobRecord[];
	total: number;
	limit: number;
	offset: number;
}

function buildQuery(filters: MyJobsFilters): string {
	const params = new URLSearchParams();
	for (const [key, value] of Object.entries(filters)) {
		if (value === undefined || value === null || value === '') continue;
		if (Array.isArray(value)) {
			for (const v of value) {
				if (v !== undefined && v !== null && v !== '') {
					params.append(key, String(v));
				}
			}
		} else {
			params.append(key, String(value));
		}
	}
	const qs = params.toString();
	return qs ? `?${qs}` : '';
}

/**
 * List the signed-in user's own jobs (user-scoped; the server forces the
 * caller's id, so no user_id filter is accepted or needed).
 */
export async function listMyJobs(filters: MyJobsFilters = {}): Promise<MyJobsResponse> {
	const response = await fetch(`${API_BASE}/api/jobs${buildQuery(filters)}`, {
		credentials: 'include',
	});
	return handle<MyJobsResponse>(response, 'List jobs');
}

export interface ProceedResponse {
	job_id: string;
	status: string;
	message: string;
}

/**
 * Override the job filter for a filtered-out job ("Proceed Anyway").
 * Re-runs CV generation so the job enters the HITL review queue.
 */
export async function proceedAnyway(
	jobId: string,
	overrideReason?: string
): Promise<ProceedResponse> {
	const response = await fetch(`${API_BASE}/api/jobs/${jobId}/proceed`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ override_reason: overrideReason || null }),
		credentials: 'include',
	});
	return handle<ProceedResponse>(response, 'Proceed with job');
}
