<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { listMyJobs, type MyJobsFilters } from '$lib/api/jobs';
	import { fetchJobStats, deleteJob, type JobStatusCounts } from '$lib/api/hitl';
	import { downloadPDF, triggerDownload } from '$lib/api/client';
	import type { AdminJobRecord } from '$lib/api/admin';
	import { POLL_INTERVAL_MS } from '$lib/config';
	import ApplicationsFilterBar from '$lib/components/applications/ApplicationsFilterBar.svelte';
	import ApplicationsTable from '$lib/components/applications/ApplicationsTable.svelte';
	import StatCard from '$lib/components/admin/StatCard.svelte';
	import ToastNotification from '$lib/components/ToastNotification.svelte';

	const PAGE_SIZE = 50;

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

	let stats = $state<JobStatusCounts>({});

	let toast = $state<{ message: string; type: 'success' | 'error' | 'info' } | null>(null);
	let pollTimer: ReturnType<typeof setInterval> | null = null;

	function showToast(message: string, type: 'success' | 'error' | 'info' = 'info') {
		toast = { message, type };
	}

	function clearToast() {
		toast = null;
	}

	function statusAccent(status: string): 'default' | 'success' | 'warning' | 'error' | 'info' {
		switch (status) {
			case 'applied':
			case 'approved':
			case 'completed':
				return 'success';
			case 'pending':
			case 'retrying':
				return 'warning';
			case 'failed':
			case 'scrape_failed':
				return 'error';
			case 'processing':
			case 'applying':
			case 'queued':
				return 'info';
			default:
				return 'default';
		}
	}

	const statCards = $derived(
		Object.entries(stats)
			.filter(([, count]) => count > 0)
			.sort((a, b) => b[1] - a[1])
	);

	function buildFilters(): MyJobsFilters {
		const f: MyJobsFilters = {
			limit: PAGE_SIZE,
			offset,
		};
		if (statuses.length > 0) f.status = statuses;
		if (sources.length > 0) f.source = sources;
		// Anchor to local midnight so the window matches the user's wall-clock day.
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
			const resp = await listMyJobs(buildFilters());
			jobs = resp.items;
			total = resp.total;
			initialLoadDone = true;
		} catch (err) {
			// Keep the last good page on failure.
			showToast(err instanceof Error ? err.message : 'Failed to load applications', 'error');
		} finally {
			loading = false;
		}
	}

	async function fetchStats() {
		try {
			stats = await fetchJobStats();
		} catch (err) {
			console.error('Failed to load job stats', err);
		}
	}

	function onFilterChange() {
		offset = 0;
		fetchJobs();
	}

	function onStatCardClick(status: string) {
		// Toggle: clicking the active single-status card clears it.
		if (statuses.length === 1 && statuses[0] === status) {
			statuses = [];
		} else {
			statuses = [status];
		}
		offset = 0;
		fetchJobs();
	}

	async function handleDelete(jobId: string) {
		if (!window.confirm(`Delete job ${jobId.slice(0, 8)}…? This removes the record and its PDF.`)) {
			return;
		}
		try {
			await deleteJob(jobId);
			showToast(`Deleted ${jobId.slice(0, 8)}…`, 'success');
			await fetchJobs({ silent: true });
			await fetchStats();
		} catch (err) {
			showToast(err instanceof Error ? err.message : 'Delete failed', 'error');
		}
	}

	function handleReview(jobId: string) {
		goto('/?job=' + jobId);
	}

	async function handleDownload(job: AdminJobRecord) {
		try {
			const blob = await downloadPDF(job.job_id);
			const company = (job.job_posting?.company as string | undefined) ?? 'CV';
			const title = (job.job_posting?.title as string | undefined) ?? job.job_id;
			const filename = `${company.replace(/[^a-z0-9]/gi, '_')}_${title.replace(/[^a-z0-9]/gi, '_')}_CV.pdf`;
			if (!triggerDownload(blob, filename)) {
				showToast('Your browser blocked the download. Please allow downloads and retry.', 'error');
			}
		} catch (err) {
			showToast(err instanceof Error ? err.message : 'Failed to download CV', 'error');
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
		fetchStats();
		fetchJobs();
		pollTimer = setInterval(() => {
			fetchJobs({ silent: true });
			fetchStats();
		}, POLL_INTERVAL_MS);
	});

	onDestroy(() => {
		if (pollTimer) clearInterval(pollTimer);
	});

	const pageStart = $derived(total === 0 ? 0 : offset + 1);
	const pageEnd = $derived(Math.min(offset + jobs.length, total));
</script>

<svelte:head>
	<title>Applications</title>
</svelte:head>

<div class="container mx-auto flex flex-col gap-4 px-4 py-6">
	<header class="flex items-center justify-between">
		<h1 class="font-heading text-2xl tracking-tight">Applications</h1>
		<button
			type="button"
			onclick={() => {
				fetchJobs();
				fetchStats();
			}}
			class="font-mono border-2 border-[var(--color-foreground)] bg-white px-3 py-1.5 text-xs uppercase tracking-wider shadow-brutal hover:-translate-y-0.5"
		>
			Refresh
		</button>
	</header>

	{#if statCards.length > 0}
		<div class="flex flex-wrap gap-3">
			{#each statCards as [status, count]}
				{@const active = statuses.length === 1 && statuses[0] === status}
				<button
					type="button"
					onclick={() => onStatCardClick(status)}
					class="text-left transition-transform hover:-translate-y-0.5 {active
						? 'ring-2 ring-[var(--color-primary)] ring-offset-2'
						: ''}"
					aria-pressed={active}
				>
					<StatCard title={status} value={count} accent={statusAccent(status)} />
				</button>
			{/each}
		</div>
	{/if}

	<ApplicationsFilterBar
		bind:statuses
		bind:sources
		bind:createdFrom
		bind:createdTo
		bind:search
		onChange={onFilterChange}
	/>

	<ApplicationsTable
		{jobs}
		onDelete={handleDelete}
		onReview={handleReview}
		onDownload={handleDownload}
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

{#if toast}
	<ToastNotification message={toast.message} type={toast.type} onClose={clearToast} />
{/if}
