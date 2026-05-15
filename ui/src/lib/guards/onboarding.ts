import type { User } from '$lib/api/auth';

/**
 * Returns true when the authenticated user still needs onboarding (no master CV).
 */
export function needsOnboarding(user: User | null): boolean {
	if (!user) return false;
	const cv = user.master_cv_json;
	if (cv == null) return true;
	if (typeof cv === 'object' && !Array.isArray(cv) && Object.keys(cv).length === 0) return true;
	return false;
}

export const ONBOARDING_PATH = '/settings?onboarding=1';
