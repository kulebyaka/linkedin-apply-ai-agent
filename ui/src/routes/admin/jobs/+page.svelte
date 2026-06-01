<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import {
		listJobs,
		retryJob,
		deleteJob,
		bulkDeleteJobs,
		listUsers,
		type AdminJobRecord,
		type ListJobsFilters,
	} from '$lib/api/admin';
	import FilterBar from '$lib/components/admin/FilterBar.svelte';
	import JobsTable from '$lib/components/admin/JobsTable.svelte';
	import ToastNotification from '$lib/components/ToastNotification.svelte';
	import { POLL_INTERVAL_MS } from '$lib/config';

	const PAGE_SIZE = 50;

	let userIds = $state<string[]>([]);
	let statuses = $state<string[]>([]);
	let sources = $state<string[]>([]);
	let createdFrom = $state('');
	let createdTo = $state('');
	let search = $state('');

	let jobs = $state<AdminJobRecord[]>([]);
	let total = $state(0);
	let offset = $state(0);
	let loading = $state(false);
	let initialLoadDone = $state(false);

	let selected = $state<Set<string>>(new Set());
	let userEmailById = $state<Record<string, string>>({});

	let toast = $state<{ message: string; type: 'success' | 'error' | 'info' } | null>(null);
	let pollTimer: ReturnType<typeof setInterval> | null = null;
	let confirmBulkDelete = $state(false);

	function showToast(message: string, type: 'success' | 'error' | 'info' = 'info') {
		toast = { message, type };
	}

	function clearToast() {
		toast = null;
	}

	function buildFilters(): ListJobsFilters {
		const f: ListJobsFilters = {
			limit: PAGE_SIZE,
			offset,
		};
		if (userIds.length > 0) f.user_id = userIds;
		if (statuses.length > 0) f.status = statuses;
		if (sources.length > 0) f.source = sources;
		// Anchor to local midnight so the window matches the admin's wall-clock day.
		if (createdFrom) {
			const [y, m, d] = createdFrom.split('-').map(Number);
			f.created_from = new Date(y, m - 1, d, 0, 0, 0, 0).toISOString();
		}
		if (createdTo) {
			const [y, m, d] = createdTo.split('-').map(Number);
			f.created_to = new Date(y, m - 1, d, 23, 59, 59, 999).toISOString();
		}
		if (search.trim()) f.search = search.trim();
		return f;
	}

	async function fetchJobs(opts: { silent?: boolean } = {}) {
		if (!opts.silent) loading = true;
		try {
			const resp = await listJobs(buildFilters());
			jobs = resp.items;
			total = resp.total;
			initialLoadDone = true;
		} catch (err) {
			showToast(err instanceof Error ? err.message : 'Failed to load jobs', 'error');
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
			console.error('Failed to load users for table', err);
		}
	}

	function onFilterChange() {
		offset = 0;
		selected = new Set();
		fetchJobs();
	}

	function onToggleSelect(jobId: string) {
		const next = new Set(selected);
		if (next.has(jobId)) {
			next.delete(jobId);
		} else {
			next.add(jobId);
		}
		selected = next;
	}

	function onToggleSelectAll() {
		const allOnPage = jobs.every((j) => selected.has(j.job_id));
		const next = new Set(selected);
		if (allOnPage) {
			for (const j of jobs) next.delete(j.job_id);
		} else {
			for (const j of jobs) next.add(j.job_id);
		}
		selected = next;
	}

	async function handleRetry(jobId: string) {
		try {
			await retryJob(jobId);
			showToast(`Retried ${jobId.slice(0, 8)}…`, 'success');
			await fetchJobs({ silent: true });
		} catch (err) {
			showToast(err instanceof Error ? err.message : 'Retry failed', 'error');
		}
	}

	async function handleDelete(jobId: string) {
		if (!window.confirm(`Delete job ${jobId.slice(0, 8)}…? This removes the record and its PDF.`)) {
			return;
		}
		try {
			await deleteJob(jobId);
			showToast(`Deleted ${jobId.slice(0, 8)}…`, 'success');
			const next = new Set(selected);
			next.delete(jobId);
			selected = next;
			await fetchJobs({ silent: true });
		} catch (err) {
			showToast(err instanceof Error ? err.message : 'Delete failed', 'error');
		}
	}

	function handleView(jobId: string) {
		// Job detail page is part of a later task; provide a placeholder action.
		showToast(`Viewing ${jobId.slice(0, 8)}… (detail page coming soon)`, 'info');
	}

	async function performBulkDelete() {
		confirmBulkDelete = false;
		const ids = Array.from(selected);
		if (ids.length === 0) return;
		try {
			const resp = await bulkDeleteJobs(ids);
			if (resp.failed.length > 0) {
				showToast(`Deleted ${resp.deleted}, ${resp.failed.length} failed`, 'info');
			} else {
				showToast(`Deleted ${resp.deleted} jobs`, 'success');
			}
			selected = new Set();
			await fetchJobs({ silent: true });
		} catch (err) {
			showToast(err instanceof Error ? err.message : 'Bulk delete failed', 'error');
		}
	}

	function goPrev() {
		if (offset === 0) return;
		offset = Math.max(0, offset - PAGE_SIZE);
		fetchJobs();
	}

	function goNext() {
		if (offset + PAGE_SIZE >= total) return;
		offset = offset + PAGE_SIZE;
		fetchJobs();
	}

	onMount(() => {
		fetchUsers();
		fetchJobs();
		pollTimer = setInterval(() => fetchJobs({ silent: true }), POLL_INTERVAL_MS);
	});

	onDestroy(() => {
		if (pollTimer) clearInterval(pollTimer);
	});

	const pageStart = $derived(total === 0 ? 0 : offset + 1);
	const pageEnd = $derived(Math.min(offset + jobs.length, total));
</script>

<svelte:head>
	<title>Admin · Jobs</title>
</svelte:head>

<div class="flex flex-col gap-4">
	<header class="flex items-center justify-between">
		<h1 class="font-heading text-2xl tracking-tight">Jobs</h1>
		<button
			type="button"
			onclick={() => fetchJobs()}
			class="font-mono border-2 border-[var(--color-foreground)] bg-white px-3 py-1.5 text-xs uppercase tracking-wider shadow-brutal hover:-translate-y-0.5"
		>
			Refresh
		</button>
	</header>

	<FilterBar
		bind:userIds
		bind:statuses
		bind:sources
		bind:createdFrom
		bind:createdTo
		bind:search
		onChange={onFilterChange}
	/>

	{#if selected.size > 0}
		<div
			class="flex items-center justify-between border-2 border-[var(--color-foreground)] bg-yellow-50 px-3 py-2 shadow-brutal"
		>
			<span class="font-mono text-xs uppercase tracking-wider">
				{selected.size} selected
			</span>
			<div class="flex gap-2">
				<button
					type="button"
					onclick={() => (selected = new Set())}
					class="font-mono border-2 border-[var(--color-foreground)] bg-white px-3 py-1 text-xs uppercase tracking-wider hover:bg-[var(--color-muted)]"
				>
					Clear
				</button>
				<button
					type="button"
					onclick={() => (confirmBulkDelete = true)}
					class="font-mono border-2 border-[var(--color-foreground)] bg-red-200 px-3 py-1 text-xs uppercase tracking-wider text-red-900 hover:bg-red-300"
				>
					Delete selected
				</button>
			</div>
		</div>
	{/if}

	<JobsTable
		{jobs}
		{userEmailById}
		{selected}
		{onToggleSelect}
		{onToggleSelectAll}
		onRetry={handleRetry}
		onDelete={handleDelete}
		onView={handleView}
		loading={loading && !initialLoadDone}
	/>

	<footer class="flex items-center justify-between">
		<span class="font-mono text-xs text-[var(--color-muted-foreground)]">
			{pageStart}–{pageEnd} of {total}
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
				disabled={offset + PAGE_SIZE >= total}
				class="font-mono border-2 border-[var(--color-foreground)] bg-white px-3 py-1 text-xs uppercase tracking-wider shadow-brutal hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
			>
				Next
			</button>
		</div>
	</footer>
</div>

{#if confirmBulkDelete}
	<div
		class="fixed inset-0 z-40 flex items-center justify-center bg-black/40"
		role="dialog"
		aria-modal="true"
	>
		<div class="w-full max-w-md border-4 border-[var(--color-foreground)] bg-white p-6 shadow-brutal">
			<h2 class="font-heading mb-2 text-lg tracking-tight">Confirm bulk delete</h2>
			<p class="font-mono mb-4 text-xs text-[var(--color-muted-foreground)]">
				Delete {selected.size} selected job{selected.size === 1 ? '' : 's'}? This removes records and
				associated PDF files. This cannot be undone.
			</p>
			<div class="flex justify-end gap-2">
				<button
					type="button"
					onclick={() => (confirmBulkDelete = false)}
					class="font-mono border-2 border-[var(--color-foreground)] bg-white px-3 py-1.5 text-xs uppercase tracking-wider hover:bg-[var(--color-muted)]"
				>
					Cancel
				</button>
				<button
					type="button"
					onclick={performBulkDelete}
					class="font-mono border-2 border-[var(--color-foreground)] bg-red-200 px-3 py-1.5 text-xs uppercase tracking-wider text-red-900 hover:bg-red-300"
				>
					Delete {selected.size}
				</button>
			</div>
		</div>
	</div>
{/if}

{#if toast}
	<ToastNotification message={toast.message} type={toast.type} onClose={clearToast} />
{/if}
