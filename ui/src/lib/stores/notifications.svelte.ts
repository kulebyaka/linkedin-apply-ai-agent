import {
	listNotifications,
	getUnreadCount,
	markNotificationRead,
	markAllNotificationsRead,
	type Notification,
} from '$lib/api/notifications';

// Persistent notification center store. Polls on a fixed interval (no SSE in
// v1) and exposes the unread count for the nav bell badge plus the full list
// for the dropdown panel.

const POLL_INTERVAL_MS = 60_000;

let items = $state<Notification[]>([]);
let unread = $state(0);
let loading = $state(false);
let pollTimer: ReturnType<typeof setInterval> | null = null;

async function refreshCount(): Promise<void> {
	try {
		unread = await getUnreadCount();
	} catch {
		// Auxiliary — keep last good value on failure.
	}
}

async function refreshList(): Promise<void> {
	loading = true;
	try {
		items = await listNotifications({ limit: 50 });
		unread = items.filter((n) => !n.read).length;
	} catch {
		// keep last good list
	} finally {
		loading = false;
	}
}

async function markRead(id: string): Promise<void> {
	const target = items.find((n) => n.id === id);
	if (target && !target.read) {
		// Optimistic update
		target.read = true;
		items = [...items];
		unread = Math.max(0, unread - 1);
	}
	try {
		await markNotificationRead(id);
	} catch {
		await refreshList();
	}
}

async function markAllRead(): Promise<void> {
	items = items.map((n) => ({ ...n, read: true }));
	unread = 0;
	try {
		await markAllNotificationsRead();
	} catch {
		await refreshList();
	}
}

function start(): void {
	if (pollTimer) return;
	void refreshCount();
	pollTimer = setInterval(() => {
		void refreshCount();
	}, POLL_INTERVAL_MS);
}

function stop(): void {
	if (pollTimer) {
		clearInterval(pollTimer);
		pollTimer = null;
	}
}

export const notifications = {
	get items() {
		return items;
	},
	get unread() {
		return unread;
	},
	get loading() {
		return loading;
	},
	refreshCount,
	refreshList,
	markRead,
	markAllRead,
	start,
	stop,
};
