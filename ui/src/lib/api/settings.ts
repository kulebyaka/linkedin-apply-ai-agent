import type { User, UserSearchPreferences } from '$lib/api/auth';

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

export async function updateProfile(data: {
	display_name?: string;
	master_cv_json?: Record<string, unknown>;
}): Promise<User> {
	const response = await fetch(`${API_BASE}/api/users/me`, {
		method: 'PUT',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(data),
		credentials: 'include',
	});

	if (!response.ok) {
		const errorText = await response.text();
		throw new Error(`Failed to update profile: ${response.statusText} - ${errorText}`);
	}

	return response.json();
}

export async function updateCV(cvJson: Record<string, unknown>): Promise<User> {
	return updateProfile({ master_cv_json: cvJson });
}

export async function getSearchPreferences(): Promise<UserSearchPreferences> {
	const response = await fetch(`${API_BASE}/api/users/me/search-preferences`, {
		credentials: 'include',
	});

	if (!response.ok) {
		const errorText = await response.text();
		throw new Error(`Failed to get search preferences: ${response.statusText} - ${errorText}`);
	}

	return response.json();
}

export async function updateSearchPreferences(
	prefs: UserSearchPreferences,
): Promise<User> {
	const response = await fetch(`${API_BASE}/api/users/me/search-preferences`, {
		method: 'PUT',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(prefs),
		credentials: 'include',
	});

	if (!response.ok) {
		const errorText = await response.text();
		throw new Error(
			`Failed to update search preferences: ${response.statusText} - ${errorText}`,
		);
	}

	return response.json();
}
