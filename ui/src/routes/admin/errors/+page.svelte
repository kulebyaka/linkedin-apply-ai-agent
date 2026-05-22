<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import {
		listErrors,
		listUsers,
		type AdminJobRecord,
	} from '$lib/api/admin';
	import ToastNotification from '$lib/components/ToastNotification.svelte';

	const POLL_MS = 10_000;
	const PAGE_SIZE = 50;

	type SinceWindow = '1h' | '24h' | '7d' | 'all';

	const sinceOptions: { value: SinceWindow; label: string }[] = [
		{ value: '1h', label: 'Last 1h' },
		{ value: '24h', label: 'Last 24h' },
		{ value: '7d', label: 'Last 7d' },
		{ value: 'all', label: 'All time' },
	];

	let since = $state<SinceWindow>('24h');
	let offset = $state(0);
	let items = $state<AdminJobRecord[]>([]);
	let loading = $state(false);
	let initialLoadDone = $state(false);
	let lastUpdatedAt = $state<number | null>(null);
	let expanded = $state<Set<string>>(new Set());
	let userEmailById = $state<Record<string, string>>({});

	let toast = $state<{ message: string; type: 'success' | 'error' | 'info' } | null>(null);
	let pollTimer: ReturnType<typeof setInterval> | null = null;

	function showToast(message: string, type: 'success' | 'error' | 'info' = 'info') {
		toast = { message, type };
	}

	function clearToast() {
		toast = null;
	}

	function sinceToIso(value: SinceWindow): string | undefined {
		if (value === 'all') return undefined;
		const now = Date.now();
		const offsets: Record<Exclude<SinceWindow, 'all'>, number> = {
			'1h': 60 * 60 * 1000,
			'24h': 24 * 60 * 60 * 1000,
			'7d': 7 * 24 * 60 * 60 * 1000,
		};
		return new Date(now - offsets[value]).toISOString();
	}

	async function fetchErrors(opts: { silent?: boolean } = {}) {
		if (!opts.silent) loading = true;
		try {
			const params: { limit: number; offset: number; since?: string } = {
				limit: PAGE_SIZE,
				offset,
			};
			const sinceIso = sinceToIso(since);
			if (sinceIso) params.since = sinceIso;
			const resp = await listErrors(params);
			items = resp.items;
			lastUpdatedAt = Date.now();
			initialLoadDone = true;
		} catch (err) {
			showToast(err instanceof Error ? err.message : 'Failed to load errors', 'error');
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

	function onSinceChange(value: SinceWindow) {
		since = value;
		offset = 0;
		expanded = new Set();
		fetchErrors();
	}

	function toggleExpand(jobId: string) {
		const next = new Set(expanded);
		if (next.has(jobId)) {
			next.delete(jobId);
		} else {
			next.add(jobId);
		}
		expanded = next;
	}

	function goPrev() {
		if (offset === 0) return;
		offset = Math.max(0, offset - PAGE_SIZE);
		fetchErrors();
	}

	function goNext() {
		if (items.length < PAGE_SIZE) return;
		offset = offset + PAGE_SIZE;
		fetchErrors();
	}

	function userEmail(userId: string | null | undefined): string {
		if (!userId) return '—';
		return userEmailById[userId] ?? userId.slice(0, 8);
	}

	function formatDate(iso: string | null | undefined): string {
		if (!iso) return '—';
		try {
			return new Date(iso).toLocaleString();
		} catch {
			return iso;
		}
	}

	function formatRelative(iso: string | null | undefined): string {
		if (!iso) return '—';
		const t = new Date(iso).getTime();
		if (Number.isNaN(t)) return iso;
		const diff = Date.now() - t;
		const abs = Math.abs(diff);
		const sec = Math.round(abs / 1000);
		if (sec < 60) return diff >= 0 ? `${sec}s ago` : `in ${sec}s`;
		const min = Math.round(sec / 60);
		if (min < 60) return diff >= 0 ? `${min}m ago` : `in ${min}m`;
		const hr = Math.round(min / 60);
		if (hr < 48) return diff >= 0 ? `${hr}h ago` : `in ${hr}h`;
		const day = Math.round(hr / 24);
		return diff >= 0 ? `${day}d ago` : `in ${day}d`;
	}

	function errorType(j: AdminJobRecord): string {
		if (j.error_message && j.last_scrape_error) return 'both';
		if (j.error_message) return 'workflow';
		if (j.last_scrape_error) return 'scrape';
		return '—';
	}

	function errorTypeBadge(type: string): string {
		const base =
			'inline-block border-2 border-[var(--color-foreground)] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider';
		if (type === 'both') return `${base} bg-red-300 text-red-900`;
		if (type === 'workflow') return `${base} bg-red-200 text-red-900`;
		if (type === 'scrape') return `${base} bg-orange-200 text-orange-900`;
		return `${base} bg-white text-[var(--color-muted-foreground)]`;
	}

	function truncate(text: string | null | undefined, max = 120): string {
		if (!text) return '';
		const flat = text.replace(/\s+/g, ' ').trim();
		if (flat.length <= max) return flat;
		return flat.slice(0, max - 1) + '…';
	}

	function jobTitle(j: AdminJobRecord): string {
		return (j.job_posting?.title as string | undefined) ?? '—';
	}

	function combinedError(j: AdminJobRecord): string {
		const parts: string[] = [];
		if (j.error_message) parts.push(`Workflow: ${j.error_message}`);
		if (j.last_scrape_error) parts.push(`Scrape: ${j.last_scrape_error}`);
		return parts.join('\n\n');
	}

	function firstErrorLine(j: AdminJobRecord): string {
		return j.error_message ?? j.last_scrape_error ?? '';
	}

	onMount(() => {
		fetchUsers();
		fetchErrors();
		pollTimer = setInterval(() => fetchErrors({ silent: true }), POLL_MS);
	});

	onDestroy(() => {
		if (pollTimer) clearInterval(pollTimer);
	});
</script>

<svelte:head>
	<title>Admin · Errors</title>
</svelte:head>

<div class="flex flex-col gap-4">
	<header class="flex items-center justify-between">
		<h1 class="font-heading text-2xl tracking-tight">Errors</h1>
		<div class="flex items-center gap-3">
			{#if lastUpdatedAt}
				<span class="font-mono text-[10px] uppercase tracking-wider text-[var(--color-muted-foreground)]">
					Auto-refresh 10s · updated {formatRelative(new Date(lastUpdatedAt).toISOString())}
				</span>
			{/if}
			<button
				type="button"
				onclick={() => fetchErrors()}
				class="font-mono border-2 border-[var(--color-foreground)] bg-white px-3 py-1.5 text-xs uppercase tracking-wider shadow-brutal hover:-translate-y-0.5"
			>
				Refresh now
			</button>
		</div>
	</header>

	<div
		class="flex flex-wrap items-center gap-2 border-2 border-[var(--color-foreground)] bg-white px-3 py-2 shadow-brutal"
	>
		<span class="font-mono text-[10px] uppercase tracking-wider text-[var(--color-muted-foreground)]">
			Since
		</span>
		{#each sinceOptions as opt}
			{@const active = since === opt.value}
			<button
				type="button"
				onclick={() => onSinceChange(opt.value)}
				class="font-mono border-2 border-[var(--color-foreground)] px-2 py-1 text-[11px] uppercase tracking-wider hover:-translate-y-0.5"
				class:bg-[var(--color-primary)]={active}
				class:text-[var(--color-primary-foreground)]={active}
				class:bg-white={!active}
			>
				{opt.label}
			</button>
		{/each}
	</div>

	<div class="overflow-x-auto border-4 border-[var(--color-foreground)] bg-white shadow-brutal">
		<table class="w-full border-collapse text-sm">
			<thead class="border-b-2 border-[var(--color-foreground)] bg-[var(--color-muted)]">
				<tr>
					<th class="px-3 py-2 text-left" aria-label="Expand"></th>
					<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">Updated</th>
					<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">User</th>
					<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">Job</th>
					<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">Type</th>
					<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">Error</th>
					<th class="font-mono px-3 py-2 text-right text-xs uppercase tracking-wider">Actions</th>
				</tr>
			</thead>
			<tbody>
				{#if loading && !initialLoadDone}
					<tr>
						<td colspan="7" class="px-3 py-8 text-center font-mono text-xs text-[var(--color-muted-foreground)]">
							Loading…
						</td>
					</tr>
				{:else if items.length === 0}
					<tr>
						<td colspan="7" class="px-3 py-8 text-center font-mono text-xs text-[var(--color-muted-foreground)]">
							No errors in this window.
						</td>
					</tr>
				{:else}
					{#each items as j (j.job_id)}
						{@const isOpen = expanded.has(j.job_id)}
						<tr class="border-b border-[var(--color-muted)] hover:bg-[var(--color-muted)]/40">
							<td class="px-3 py-2">
								<button
									type="button"
									onclick={() => toggleExpand(j.job_id)}
									aria-label={isOpen ? 'Collapse row' : 'Expand row'}
									aria-expanded={isOpen}
									class="font-mono border-2 border-[var(--color-foreground)] bg-white px-2 py-0.5 text-[10px] hover:bg-[var(--color-muted)]"
								>
									{isOpen ? '−' : '+'}
								</button>
							</td>
							<td class="px-3 py-2 font-mono text-xs" title={formatDate(j.updated_at)}>
								{formatRelative(j.updated_at)}
							</td>
							<td class="px-3 py-2 font-mono text-xs">{userEmail(j.user_id)}</td>
							<td class="px-3 py-2 text-sm">{jobTitle(j)}</td>
							<td class="px-3 py-2">
								<span class={errorTypeBadge(errorType(j))}>{errorType(j)}</span>
							</td>
							<td class="px-3 py-2 font-mono text-xs">{truncate(firstErrorLine(j))}</td>
							<td class="px-3 py-2 text-right">
								<a
									href={`/admin/jobs?focus=${encodeURIComponent(j.job_id)}`}
									class="font-mono border-2 border-[var(--color-foreground)] bg-white px-2 py-1 text-[10px] uppercase tracking-wider hover:bg-[var(--color-muted)] inline-block"
								>
									Job
								</a>
							</td>
						</tr>
						{#if isOpen}
							<tr class="border-b border-[var(--color-muted)] bg-[var(--color-muted)]/30">
								<td colspan="7" class="px-3 py-3">
									<div class="flex flex-col gap-2 font-mono text-xs">
										<div>
											<span class="text-[var(--color-muted-foreground)]">job_id:</span>
											<span class="ml-1 break-all">{j.job_id}</span>
										</div>
										<div>
											<span class="text-[var(--color-muted-foreground)]">status:</span>
											<span class="ml-1">{j.status}</span>
										</div>
										{#if j.error_message}
											<div>
												<div class="text-[var(--color-muted-foreground)]">Workflow error:</div>
												<pre class="mt-1 max-h-64 overflow-auto whitespace-pre-wrap border-2 border-[var(--color-foreground)] bg-white p-2 text-[11px]">{j.error_message}</pre>
											</div>
										{/if}
										{#if j.last_scrape_error}
											<div>
												<div class="text-[var(--color-muted-foreground)]">Scrape error:</div>
												<pre class="mt-1 max-h-64 overflow-auto whitespace-pre-wrap border-2 border-[var(--color-foreground)] bg-white p-2 text-[11px]">{j.last_scrape_error}</pre>
											</div>
										{/if}
										{#if !j.error_message && !j.last_scrape_error}
											<div class="text-[var(--color-muted-foreground)]">No error text recorded.</div>
										{/if}
									</div>
								</td>
							</tr>
						{/if}
					{/each}
				{/if}
			</tbody>
		</table>
	</div>

	<footer class="flex items-center justify-between">
		<span class="font-mono text-xs text-[var(--color-muted-foreground)]">
			Showing {items.length} record{items.length === 1 ? '' : 's'} from offset {offset}
		</span>
		<div class="flex gap-2">
			<button
				type="button"
				onclick={goPrev}
				disabled={offset === 0}
				class="font-mono border-2 border-[var(--color-foreground)] bg-white px-3 py-1 text-xs uppercase tracking-wider shadow-brutal hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
			>
				Prev
			</button>
			<button
				type="button"
				onclick={goNext}
				disabled={items.length < PAGE_SIZE}
				class="font-mono border-2 border-[var(--color-foreground)] bg-white px-3 py-1 text-xs uppercase tracking-wider shadow-brutal hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
			>
				Next
			</button>
		</div>
	</footer>
</div>

{#if toast}
	<ToastNotification message={toast.message} type={toast.type} onClose={clearToast} />
{/if}
