const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

export interface User {
	id: string;
	email: string;
	display_name: string;
	master_cv_json: Record<string, unknown> | null;
	search_preferences: UserSearchPreferences | null;
	created_at: string;
	updated_at: string;
}

export interface UserSearchPreferences {
	keywords: string;
	location: string;
	remote_filter: string | null;
	date_posted: string | null;
	experience_level: string[] | null;
	job_type: string[] | null;
	easy_apply_only: boolean;
	max_jobs: number;
}

export interface AuthResponse {
	user: User;
	message: string;
}

export async function requestMagicLink(email: string): Promise<{ message: string }> {
	const response = await fetch(`${API_BASE}/api/auth/login`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ email }),
		credentials: 'include',
	});

	if (!response.ok) {
		const errorText = await response.text();
		throw new Error(`Failed to send magic link: ${response.statusText} - ${errorText}`);
	}

	return response.json();
}

export async function verifyToken(token: string): Promise<AuthResponse> {
	const response = await fetch(`${API_BASE}/api/auth/verify?token=${encodeURIComponent(token)}`, {
		credentials: 'include',
	});

	if (!response.ok) {
		const errorText = await response.text();
		throw new Error(`Token verification failed: ${response.statusText} - ${errorText}`);
	}

	return response.json();
}

export async function getCurrentUser(): Promise<User> {
	const response = await fetch(`${API_BASE}/api/auth/me`, {
		credentials: 'include',
	});

	if (!response.ok) {
		throw new Error('Not authenticated');
	}

	return response.json();
}

export async function logout(): Promise<void> {
	await fetch(`${API_BASE}/api/auth/logout`, {
		method: 'POST',
		credentials: 'include',
	});
}
