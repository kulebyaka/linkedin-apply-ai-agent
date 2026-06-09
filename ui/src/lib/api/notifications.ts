const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

export interface Notification {
	id: string;
	user_id: string;
	type: string;
	title: string;
	body: string | null;
	action_url: string | null;
	read: boolean;
	created_at: string;
}

async function handle<T>(response: Response, action: string): Promise<T> {
	if (!response.ok) {
		const errorText = await response.text();
		throw new Error(`${action} failed: ${response.statusText} - ${errorText}`);
	}
	return response.json();
}

export async function listNotifications(opts: { unreadOnly?: boolean; limit?: number } = {}): Promise<Notification[]> {
	const params = new URLSearchParams();
	if (opts.unreadOnly) params.set('unread_only', 'true');
	if (opts.limit) params.set('limit', String(opts.limit));
	const qs = params.toString();
	const response = await fetch(`${API_BASE}/api/notifications${qs ? `?${qs}` : ''}`, {
		credentials: 'include',
	});
	return handle<Notification[]>(response, 'List notifications');
}

export async function getUnreadCount(): Promise<number> {
	const response = await fetch(`${API_BASE}/api/notifications/unread-count`, {
		credentials: 'include',
	});
	const data = await handle<{ count: number }>(response, 'Unread count');
	return data.count;
}

export async function markNotificationRead(id: string): Promise<void> {
	const response = await fetch(`${API_BASE}/api/notifications/${encodeURIComponent(id)}/read`, {
		method: 'PUT',
		credentials: 'include',
	});
	await handle(response, 'Mark notification read');
}

export async function markAllNotificationsRead(): Promise<number> {
	const response = await fetch(`${API_BASE}/api/notifications/read-all`, {
		method: 'PUT',
		credentials: 'include',
	});
	const data = await handle<{ updated: number }>(response, 'Mark all read');
	return data.updated;
}
