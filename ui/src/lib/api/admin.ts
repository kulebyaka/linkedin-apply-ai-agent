import type { User, UserRole } from '$lib/api/auth';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

export type JobStatus =
	| 'queued'
	| 'processing'
	| 'completed'
	| 'pending'
	| 'approved'
	| 'declined'
	| 'retrying'
	| 'applying'
	| 'applied'
	| 'failed'
	| 'filtered_out'
	| 'scrape_failed';

export type JobSource = 'linkedin' | 'url' | 'manual';

export interface AdminJobRecord {
	job_id: string;
	user_id: string | null;
	source: JobSource | string;
	mode: string;
	status: JobStatus | string;
	workflow_step?: string | null;
	job_posting?: {
		title?: string;
		company?: string;
		description?: string;
		[key: string]: unknown;
	} | null;
	pdf_path?: string | null;
	error_message?: string | null;
	last_scrape_error?: string | null;
	/** Whether the LinkedIn session was authenticated when this job was scraped. */
	session_authenticated?: boolean | null;
	attempt_count?: number;
	created_at: string;
	updated_at?: string | null;
	[key: string]: unknown;
}

export interface ListJobsFilters {
	user_id?: string[];
	status?: string[];
	source?: string[];
	created_from?: string;
	created_to?: string;
	search?: string;
	limit?: number;
	offset?: number;
}

export interface ListJobsResponse {
	items: AdminJobRecord[];
	total: number;
	limit: number;
	offset: number;
}

export interface BulkDeleteResponse {
	deleted: number;
	failed: string[];
}

export interface ConsumerSnapshot {
	is_running: boolean;
	task_count: number;
	queue_depth: number;
}

export interface SchedulerJobState {
	user_id: string;
	last_run_at: string | null;
	next_run_at: string | null;
	last_status: string | null;
}

export interface LinkedInAuthState {
	authenticated: boolean;
	job_id: string;
	scraped_at: string | null;
}

export interface QueueStateResponse {
	consumer: ConsumerSnapshot;
	scheduler: SchedulerJobState[];
	/** Global LinkedIn session state derived from the most recently scraped job. Null until a job has recorded it. */
	linkedin_auth: LinkedInAuthState | null;
	counts: {
		last_24h: Record<string, number>;
		last_7d: Record<string, number>;
		all_time: Record<string, number>;
	};
}

export interface ListErrorsParams {
	limit?: number;
	offset?: number;
	since?: string;
}

export interface ListErrorsResponse {
	items: AdminJobRecord[];
	limit: number;
	offset: number;
}

export interface AdminUserSummary {
	id: string;
	email: string;
	display_name: string;
	role: UserRole;
	created_at: string | null;
	updated_at: string | null;
}

export interface AdminUserRow {
	user: AdminUserSummary;
	job_counts: Record<string, number>;
	last_job_at: string | null;
}

export interface ListUsersResponse {
	items: AdminUserRow[];
	limit: number;
	offset: number;
}

function buildQuery(filters: ListJobsFilters | ListErrorsParams): string {
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

async function handle<T>(response: Response, action: string): Promise<T> {
	if (!response.ok) {
		const errorText = await response.text();
		throw new Error(`${action} failed: ${response.statusText} - ${errorText}`);
	}
	return response.json();
}

export async function listJobs(filters: ListJobsFilters = {}): Promise<ListJobsResponse> {
	const response = await fetch(`${API_BASE}/api/admin/jobs${buildQuery(filters)}`, {
		credentials: 'include',
	});
	return handle<ListJobsResponse>(response, 'List admin jobs');
}

export async function getJob(jobId: string): Promise<AdminJobRecord> {
	const response = await fetch(`${API_BASE}/api/admin/jobs/${encodeURIComponent(jobId)}`, {
		credentials: 'include',
	});
	return handle<AdminJobRecord>(response, 'Get admin job');
}

export async function retryJob(jobId: string): Promise<AdminJobRecord> {
	const response = await fetch(
		`${API_BASE}/api/admin/jobs/${encodeURIComponent(jobId)}/retry`,
		{ method: 'POST', credentials: 'include' },
	);
	return handle<AdminJobRecord>(response, 'Retry job');
}

export async function deleteJob(jobId: string): Promise<{ deleted: boolean; job_id: string }> {
	const response = await fetch(`${API_BASE}/api/admin/jobs/${encodeURIComponent(jobId)}`, {
		method: 'DELETE',
		credentials: 'include',
	});
	return handle<{ deleted: boolean; job_id: string }>(response, 'Delete job');
}

export async function bulkDeleteJobs(jobIds: string[]): Promise<BulkDeleteResponse> {
	const response = await fetch(`${API_BASE}/api/admin/jobs/bulk-delete`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ job_ids: jobIds }),
		credentials: 'include',
	});
	return handle<BulkDeleteResponse>(response, 'Bulk delete jobs');
}

export async function getQueueState(): Promise<QueueStateResponse> {
	const response = await fetch(`${API_BASE}/api/admin/queue`, { credentials: 'include' });
	return handle<QueueStateResponse>(response, 'Get queue state');
}

export async function runScheduler(userId: string): Promise<{ status: string; user_id: string }> {
	const response = await fetch(
		`${API_BASE}/api/admin/scheduler/run/${encodeURIComponent(userId)}`,
		{ method: 'POST', credentials: 'include' },
	);
	return handle<{ status: string; user_id: string }>(response, 'Run scheduler');
}

export async function listErrors(params: ListErrorsParams = {}): Promise<ListErrorsResponse> {
	const response = await fetch(`${API_BASE}/api/admin/errors${buildQuery(params)}`, {
		credentials: 'include',
	});
	return handle<ListErrorsResponse>(response, 'List admin errors');
}

export async function listUsers(
	limit: number = 200,
	offset: number = 0,
): Promise<ListUsersResponse> {
	const qs = buildQuery({ limit, offset } as ListErrorsParams);
	const response = await fetch(`${API_BASE}/api/admin/users${qs}`, { credentials: 'include' });
	return handle<ListUsersResponse>(response, 'List admin users');
}

export async function setUserRole(userId: string, role: UserRole): Promise<AdminUserSummary> {
	const response = await fetch(
		`${API_BASE}/api/admin/users/${encodeURIComponent(userId)}/role`,
		{
			method: 'PUT',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ role }),
			credentials: 'include',
		},
	);
	return handle<AdminUserSummary>(response, 'Set user role');
}

export type { User };
