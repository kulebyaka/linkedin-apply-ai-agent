/**
 * Shared fetch-response handler for the API client modules.
 *
 * Throws a descriptive Error on a non-OK response (including the body text),
 * otherwise parses and returns the JSON payload.
 */
export async function handle<T>(response: Response, action: string): Promise<T> {
	if (!response.ok) {
		const errorText = await response.text();
		throw new Error(`${action} failed: ${response.statusText} - ${errorText}`);
	}
	return response.json();
}
