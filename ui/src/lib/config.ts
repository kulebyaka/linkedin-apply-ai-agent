/**
 * Shared UI configuration.
 *
 * POLL_INTERVAL_MS centralizes the cadence for all polling UIs (admin pages,
 * applications page, etc.). Override via the VITE_POLL_INTERVAL_MS env var.
 */
export const POLL_INTERVAL_MS = Number(import.meta.env.VITE_POLL_INTERVAL_MS ?? 5000);
