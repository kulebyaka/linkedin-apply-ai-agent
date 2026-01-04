export function validateJobDescription(description: string): string | null {
	const trimmed = description.trim();

	if (trimmed.length === 0) {
		return 'Job description is required';
	}

	if (trimmed.length < 50) {
		return 'Job description must be at least 50 characters';
	}

	return null;
}
