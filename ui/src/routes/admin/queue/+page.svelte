<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import {
		getQueueState,
		runScheduler,
		listUsers,
		type QueueStateResponse,
		type SchedulerJobState,
	} from '$lib/api/admin';
	import StatCard from '$lib/components/admin/StatCard.svelte';
	import ToastNotification from '$lib/components/ToastNotification.svelte';

	const POLL_MS = 5_000;

	let queueState = $state<QueueStateResponse | null>(null);
	let userEmailById = $state<Record<string, string>>({});
	let loading = $state(false);
	let initialLoadDone = $state(false);
	let lastUpdatedAt = $state<number | null>(null);
	let runningUserIds = $state<Set<string>>(new Set());
	let toast = $state<{ message: string; type: 'success' | 'error' | 'info' } | null>(null);
	let pollTimer: ReturnType<typeof setInterval> | null = null;

	function showToast(message: string, type: 'success' | 'error' | 'info' = 'info') {
		toast = { message, type };
	}

	function clearToast() {
		toast = null;
	}

	async function fetchState(opts: { silent?: boolean } = {}) {
		if (!opts.silent) loading = true;
		try {
			const resp = await getQueueState();
			queueState = resp;
			lastUpdatedAt = Date.now();
			initialLoadDone = true;
		} catch (err) {
			showToast(err instanceof Error ? err.message : 'Failed to load queue state', 'error');
		} finally {
			loading = false;
		}
	}

	async function fetchUsers() {
		try {
			const resp = await listUsers(200, 0);
			const map: Record<string, string> = {};
			for (const row of resp.items) {
				map[row.user.id] = row.user.email;
			}
			userEmailById = map;
		} catch (err) {
			console.error('Failed to load users', err);
		}
	}

	async function handleRunNow(userId: string) {
		const next = new Set(runningUserIds);
		next.add(userId);
		runningUserIds = next;
		try {
			const resp = await runScheduler(userId);
			showToast(`Scheduler ${resp.status} for ${userEmail(userId)}`, 'success');
			await fetchState({ silent: true });
		} catch (err) {
			showToast(err instanceof Error ? err.message : 'Run failed', 'error');
		} finally {
			const after = new Set(runningUserIds);
			after.delete(userId);
			runningUserIds = after;
		}
	}

	function userEmail(userId: string): string {
		return userEmailById[userId] ?? userId.slice(0, 8);
	}

	function formatRelative(iso: string | null): string {
		if (!iso) return '—';
		const t = new Date(iso).getTime();
		if (Number.isNaN(t)) return iso;
		const diffMs = Date.now() - t;
		const absMs = Math.abs(diffMs);
		const sec = Math.round(absMs / 1000);
		if (sec < 60) return diffMs >= 0 ? `${sec}s ago` : `in ${sec}s`;
		const min = Math.round(sec / 60);
		if (min < 60) return diffMs >= 0 ? `${min}m ago` : `in ${min}m`;
		const hr = Math.round(min / 60);
		if (hr < 48) return diffMs >= 0 ? `${hr}h ago` : `in ${hr}h`;
		const day = Math.round(hr / 24);
		return diffMs >= 0 ? `${day}d ago` : `in ${day}d`;
	}

	function formatAbsolute(iso: string | null): string {
		if (!iso) return '';
		try {
			return new Date(iso).toLocaleString();
		} catch {
			return iso;
		}
	}

	function lastStatusBadgeClass(s: string | null): string {
		const base =
			'inline-block border-2 border-[var(--color-foreground)] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider';
		if (!s) return `${base} bg-white text-[var(--color-muted-foreground)]`;
		const lower = s.toLowerCase();
		if (lower === 'ok' || lower === 'success') return `${base} bg-emerald-200 text-emerald-900`;
		if (lower === 'error' || lower === 'failed') return `${base} bg-red-200 text-red-900`;
		if (lower === 'running' || lower === 'pending') return `${base} bg-blue-100 text-blue-900`;
		return `${base} bg-white`;
	}

	function totalCount(counts: Record<string, number> | undefined): number {
		if (!counts) return 0;
		let n = 0;
		for (const v of Object.values(counts)) n += v;
		return n;
	}

	function sortedCounts(counts: Record<string, number> | undefined): [string, number][] {
		if (!counts) return [];
		return Object.entries(counts)
			.filter(([, v]) => v > 0)
			.sort((a, b) => b[1] - a[1]);
	}

	function statusColor(status: string): string {
		const colorMap: Record<string, string> = {
			queued: 'bg-zinc-100',
			processing: 'bg-blue-100',
			cv_ready: 'bg-green-100',
			pending_review: 'bg-yellow-100',
			pending: 'bg-yellow-100',
			approved: 'bg-emerald-200',
			declined: 'bg-zinc-200',
			retrying: 'bg-orange-100',
			applying: 'bg-indigo-100',
			applied: 'bg-emerald-300',
			failed: 'bg-red-200',
			scrape_failed: 'bg-red-100',
			filtered_out: 'bg-zinc-200',
			completed: 'bg-emerald-200',
		};
		return colorMap[status] ?? 'bg-white';
	}

	onMount(() => {
		fetchUsers();
		fetchState();
		pollTimer = setInterval(() => fetchState({ silent: true }), POLL_MS);
	});

	onDestroy(() => {
		if (pollTimer) clearInterval(pollTimer);
	});

	const consumer = $derived(queueState?.consumer ?? null);
	const schedulerRows = $derived<SchedulerJobState[]>(queueState?.scheduler ?? []);
	const counts24h = $derived(queueState?.counts?.last_24h);
	const counts7d = $derived(queueState?.counts?.last_7d);
</script>

<svelte:head>
	<title>Admin · Queue</title>
</svelte:head>

<div class="flex flex-col gap-4">
	<header class="flex items-center justify-between">
		<h1 class="font-heading text-2xl tracking-tight">Queue & Scheduler</h1>
		<div class="flex items-center gap-3">
			{#if lastUpdatedAt}
				<span class="font-mono text-[10px] uppercase tracking-wider text-[var(--color-muted-foreground)]">
					Auto-refresh 5s · updated {formatRelative(new Date(lastUpdatedAt).toISOString())}
				</span>
			{/if}
			<button
				type="button"
				onclick={() => fetchState()}
				class="font-mono border-2 border-[var(--color-foreground)] bg-white px-3 py-1.5 text-xs uppercase tracking-wider shadow-brutal hover:-translate-y-0.5"
			>
				Refresh now
			</button>
		</div>
	</header>

	{#if loading && !initialLoadDone}
		<div class="border-2 border-[var(--color-foreground)] bg-white p-6 text-center font-mono text-xs text-[var(--color-muted-foreground)] shadow-brutal">
			Loading…
		</div>
	{:else if queueState}
		<section class="flex flex-wrap gap-3">
			<StatCard
				title="Queue depth"
				value={consumer?.queue_depth ?? 0}
				subLabel="awaiting pickup"
				accent={consumer && consumer.queue_depth > 0 ? 'info' : 'default'}
			/>
			<StatCard
				title="Consumer"
				value={consumer?.is_running ? 'Running' : 'Stopped'}
				subLabel={consumer?.is_running ? 'workers active' : 'no workers'}
				accent={consumer?.is_running ? 'success' : 'error'}
			/>
			<StatCard
				title="Active tasks"
				value={consumer?.task_count ?? 0}
				subLabel="in flight"
			/>
			<StatCard
				title="Jobs · 24h"
				value={totalCount(counts24h)}
				subLabel="all statuses"
			/>
			<StatCard
				title="Jobs · 7d"
				value={totalCount(counts7d)}
				subLabel="all statuses"
			/>
		</section>

		<section class="grid gap-4 lg:grid-cols-2">
			<div class="border-4 border-[var(--color-foreground)] bg-white p-4 shadow-brutal">
				<h2 class="font-heading mb-3 text-lg tracking-tight">Status · last 24h</h2>
				{#if sortedCounts(counts24h).length === 0}
					<p class="font-mono text-xs text-[var(--color-muted-foreground)]">No jobs in the last 24h.</p>
				{:else}
					<ul class="flex flex-col gap-1.5">
						{#each sortedCounts(counts24h) as [status, count]}
							<li class="flex items-center gap-2">
								<span
									class="font-mono inline-block w-32 border-2 border-[var(--color-foreground)] {statusColor(status)} px-1.5 py-0.5 text-[10px] uppercase tracking-wider"
								>
									{status}
								</span>
								<span class="font-mono text-sm">{count}</span>
							</li>
						{/each}
					</ul>
				{/if}
			</div>

			<div class="border-4 border-[var(--color-foreground)] bg-white p-4 shadow-brutal">
				<h2 class="font-heading mb-3 text-lg tracking-tight">Status · last 7d</h2>
				{#if sortedCounts(counts7d).length === 0}
					<p class="font-mono text-xs text-[var(--color-muted-foreground)]">No jobs in the last 7d.</p>
				{:else}
					<ul class="flex flex-col gap-1.5">
						{#each sortedCounts(counts7d) as [status, count]}
							<li class="flex items-center gap-2">
								<span
									class="font-mono inline-block w-32 border-2 border-[var(--color-foreground)] {statusColor(status)} px-1.5 py-0.5 text-[10px] uppercase tracking-wider"
								>
									{status}
								</span>
								<span class="font-mono text-sm">{count}</span>
							</li>
						{/each}
					</ul>
				{/if}
			</div>
		</section>

		<section class="border-4 border-[var(--color-foreground)] bg-white shadow-brutal">
			<header class="flex items-center justify-between border-b-2 border-[var(--color-foreground)] bg-[var(--color-muted)] px-4 py-2">
				<h2 class="font-heading text-lg tracking-tight">Scheduler · per user</h2>
				<span class="font-mono text-[10px] uppercase tracking-wider text-[var(--color-muted-foreground)]">
					{schedulerRows.length} user{schedulerRows.length === 1 ? '' : 's'}
				</span>
			</header>
			<div class="overflow-x-auto">
				<table class="w-full border-collapse text-sm">
					<thead class="border-b-2 border-[var(--color-foreground)] bg-[var(--color-muted)]/40">
						<tr>
							<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">User</th>
							<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">Last run</th>
							<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">Next run</th>
							<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">Last status</th>
							<th class="font-mono px-3 py-2 text-right text-xs uppercase tracking-wider">Actions</th>
						</tr>
					</thead>
					<tbody>
						{#if schedulerRows.length === 0}
							<tr>
								<td colspan="5" class="px-3 py-6 text-center font-mono text-xs text-[var(--color-muted-foreground)]">
									No scheduled jobs.
								</td>
							</tr>
						{:else}
							{#each schedulerRows as row (row.user_id)}
								<tr class="border-b border-[var(--color-muted)] hover:bg-[var(--color-muted)]/30">
									<td class="px-3 py-2 font-mono text-xs">{userEmail(row.user_id)}</td>
									<td class="px-3 py-2 font-mono text-xs" title={formatAbsolute(row.last_run_at)}>
										{formatRelative(row.last_run_at)}
									</td>
									<td class="px-3 py-2 font-mono text-xs" title={formatAbsolute(row.next_run_at)}>
										{formatRelative(row.next_run_at)}
									</td>
									<td class="px-3 py-2">
										<span class={lastStatusBadgeClass(row.last_status)}>{row.last_status ?? '—'}</span>
									</td>
									<td class="px-3 py-2 text-right">
										<button
											type="button"
											onclick={() => handleRunNow(row.user_id)}
											disabled={runningUserIds.has(row.user_id)}
											class="font-mono border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-2 py-1 text-[10px] uppercase tracking-wider text-[var(--color-primary-foreground)] hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
										>
											{runningUserIds.has(row.user_id) ? 'Running…' : 'Run now'}
										</button>
									</td>
								</tr>
							{/each}
						{/if}
					</tbody>
				</table>
			</div>
		</section>
	{/if}
</div>

{#if toast}
	<ToastNotification message={toast.message} type={toast.type} onClose={clearToast} />
{/if}
