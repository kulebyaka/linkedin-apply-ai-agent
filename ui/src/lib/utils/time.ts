/**
 * Format a timestamp relative to now, e.g. "5 min ago" / "in 2 h".
 *
 * Accepts a Date or an ISO string. Handles both past and future instants so it
 * can render "last run" timestamps and "next run" countdowns alike.
 */
export function relativeTime(date: Date | string): string {
	const d = typeof date === 'string' ? new Date(date) : date;
	const diffMs = d.getTime() - Date.now();
	const past = diffMs < 0;
	const absMin = Math.round(Math.abs(diffMs) / 60_000);
	if (absMin < 1) return past ? 'just now' : 'in less than a minute';
	if (absMin < 60) return past ? `${absMin} min ago` : `in ${absMin} min`;
	const absHr = Math.round(absMin / 60);
	if (absHr < 24) return past ? `${absHr} h ago` : `in ${absHr} h`;
	const absDay = Math.round(absHr / 24);
	return past ? `${absDay} d ago` : `in ${absDay} d`;
}
