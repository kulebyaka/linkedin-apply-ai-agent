import type {
	LLMOperation,
	ModelCatalogEntry,
	ModelChoice,
	User,
	UserModelPreferences,
	UserSearchPreferences,
} from '$lib/api/auth';
import type { UserFilterPreferences } from '$lib/types/index';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

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

export async function getFilterPreferences(): Promise<UserFilterPreferences> {
	const response = await fetch(`${API_BASE}/api/users/me/filter-preferences`, {
		credentials: 'include',
	});

	if (!response.ok) {
		const errorText = await response.text();
		throw new Error(`Failed to get filter preferences: ${response.statusText} - ${errorText}`);
	}

	return response.json();
}

export async function updateFilterPreferences(prefs: UserFilterPreferences): Promise<User> {
	const response = await fetch(`${API_BASE}/api/users/me/filter-preferences`, {
		method: 'PUT',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(prefs),
		credentials: 'include',
	});

	if (!response.ok) {
		const errorText = await response.text();
		throw new Error(
			`Failed to update filter preferences: ${response.statusText} - ${errorText}`,
		);
	}

	return response.json();
}

export interface ModelCatalogResponse {
	models: ModelCatalogEntry[];
	default: ModelChoice;
}

export async function getModelCatalog(
	operation?: LLMOperation,
): Promise<ModelCatalogResponse> {
	const base = API_BASE || (typeof window !== 'undefined' ? window.location.origin : 'http://localhost');
	const url = new URL(`${API_BASE}/api/llm/models`, base);
	if (operation) url.searchParams.set('operation', operation);

	const response = await fetch(url.toString(), { credentials: 'include' });

	if (!response.ok) {
		const errorText = await response.text();
		throw new Error(`Failed to load model catalog: ${response.statusText} - ${errorText}`);
	}

	return response.json();
}

export async function updateModelPreferences(
	prefs: UserModelPreferences,
): Promise<User> {
	const response = await fetch(`${API_BASE}/api/users/me`, {
		method: 'PUT',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ model_preferences: prefs }),
		credentials: 'include',
	});

	if (!response.ok) {
		const errorText = await response.text();
		throw new Error(
			`Failed to update model preferences: ${response.statusText} - ${errorText}`,
		);
	}

	return response.json();
}

export interface CVExtractionStartResponse {
	extraction_id: string;
	status: 'pending';
}

export interface CVExtractionStatusResponse {
	extraction_id: string;
	status: 'pending' | 'running' | 'completed' | 'failed';
	result_json: Record<string, unknown> | null;
	validation_errors: string[];
	error_message: string | null;
}

async function extractDetail(response: Response, fallback: string): Promise<string> {
	try {
		const body = await response.json();
		if (body && typeof body.detail === 'string') return body.detail;
	} catch {
		/* not JSON */
	}
	return `${fallback}: ${response.statusText}`;
}

export async function extractCVFromPDF(file: File): Promise<CVExtractionStartResponse> {
	const form = new FormData();
	form.append('file', file);

	const response = await fetch(`${API_BASE}/api/users/me/master-cv/extract`, {
		method: 'POST',
		body: form,
		credentials: 'include',
	});

	if (!response.ok) {
		throw new Error(await extractDetail(response, 'Failed to upload PDF'));
	}

	return response.json();
}

export async function getCVExtractionStatus(
	extractionId: string,
): Promise<CVExtractionStatusResponse> {
	const response = await fetch(
		`${API_BASE}/api/users/me/master-cv/extract/${encodeURIComponent(extractionId)}`,
		{ credentials: 'include' },
	);

	if (!response.ok) {
		throw new Error(await extractDetail(response, 'Failed to get extraction status'));
	}

	return response.json();
}

export async function generateFilterPrompt(naturalLanguagePrefs: string): Promise<{ prompt: string }> {
	const response = await fetch(`${API_BASE}/api/users/me/filter-preferences/generate-prompt`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ natural_language_prefs: naturalLanguagePrefs }),
		credentials: 'include',
	});

	if (!response.ok) {
		const errorText = await response.text();
		throw new Error(`Failed to generate filter prompt: ${response.statusText} - ${errorText}`);
	}

	return response.json();
}
