import type { AdminJobRecord, PendingQuestion } from './admin';
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

export interface ApplyResponse {
	job_id: string;
	status: string;
	message: string;
}

/**
 * (Re-)trigger an Easy Apply run for a job awaiting application.
 * Used to recover a job parked in `needs_extension` once the browser
 * extension is connected (or to re-run an `approved` job).
 */
export async function applyJob(jobId: string): Promise<ApplyResponse> {
	const response = await fetch(`${API_BASE}/api/jobs/${jobId}/apply`, {
		method: 'POST',
		credentials: 'include',
	});
	return handle<ApplyResponse>(response, 'Apply to job');
}

export interface QuestionAnswer {
	label: string;
	field_type: string;
	value: string;
	options?: string[];
	kind?: string | null;
}

/** Minimal ApplyProfile shape returned by answerQuestions (custom answers included). */
export interface ApplyProfile {
	phone_country_code?: string | null;
	years_experience?: number | null;
	expected_salary?: string | null;
	needs_visa_sponsorship?: boolean | null;
	legally_authorized?: boolean | null;
	willing_to_relocate?: boolean | null;
	drivers_license?: boolean | null;
	custom_answers?: Array<{
		key: string;
		label: string;
		field_type: string;
		value: string;
		options?: string[];
	}>;
}

/**
 * Save the user's answers to a job's parked `manual_required` questions.
 * Answers are stored on the user's ApplyProfile and reused on future
 * applications. The job stays in `manual_required`; call `applyJob` after
 * to re-dispatch it.
 */
export async function answerQuestions(
	jobId: string,
	answers: QuestionAnswer[]
): Promise<ApplyProfile> {
	const response = await fetch(`${API_BASE}/api/jobs/${jobId}/answer-questions`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ answers }),
		credentials: 'include',
	});
	return handle<ApplyProfile>(response, 'Answer questions');
}

// Re-export for consumers that build the questions UI.
export type { PendingQuestion };
