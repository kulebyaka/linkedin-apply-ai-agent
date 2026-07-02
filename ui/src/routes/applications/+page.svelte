<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { applyJob, listMyJobs, proceedAnyway, type MyJobsFilters } from '$lib/api/jobs';
	import { fetchJobStats, deleteJob, type JobStatusCounts } from '$lib/api/hitl';
	import { downloadPDF, triggerDownload } from '$lib/api/client';
	import type { AdminJobRecord, FilterResult } from '$lib/api/admin';
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

	// "Proceed Anyway" confirmation modal state.
	let proceedJob = $state<AdminJobRecord | null>(null);
	let proceedSubmitting = $state(false);
	let proceedReason = $state('');

	const proceedFilter = $derived<FilterResult | null>(
		proceedJob?.filter_result && typeof proceedJob.filter_result === 'object'
			? (proceedJob.filter_result as FilterResult)
			: null
	);

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
			case 'manual_required':
			case 'needs_extension':
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

	/** True when any loaded job is parked waiting for the browser extension. */
	const hasNeedsExtension = $derived(jobs.some((j) => j.status === 'needs_extension'));

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

	async function handleApply(job: AdminJobRecord) {
		try {
			const resp = await applyJob(job.job_id);
			if (resp.status === 'applying') {
				showToast('Application started — watch this job for the result.', 'success');
			} else {
				showToast(resp.message || 'Connect the extension in your browser to apply.', 'info');
			}
			await fetchJobs({ silent: true });
			await fetchStats();
		} catch (err) {
			showToast(err instanceof Error ? err.message : 'Failed to start application', 'error');
		}
	}

	function handleProceed(job: AdminJobRecord) {
		proceedJob = job;
		proceedReason = '';
	}

	function cancelProceed() {
		if (proceedSubmitting) return;
		proceedJob = null;
		proceedReason = '';
	}

	async function confirmProceed() {
		if (!proceedJob) return;
		const jobId = proceedJob.job_id;
		proceedSubmitting = true;
		try {
			await proceedAnyway(jobId, proceedReason.trim() || undefined);
			showToast('CV generation started — the job will appear in your review queue.', 'success');
			proceedJob = null;
			proceedReason = '';
			await fetchJobs({ silent: true });
			await fetchStats();
		} catch (err) {
			showToast(err instanceof Error ? err.message : 'Failed to proceed with job', 'error');
		} finally {
			proceedSubmitting = false;
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

	{#if hasNeedsExtension}
		<div
			class="flex flex-wrap items-center justify-between gap-3 border-4 border-[var(--color-foreground)] bg-purple-100 px-4 py-3 shadow-brutal"
			role="status"
		>
			<p class="font-body text-sm text-purple-900">
				Some jobs are waiting to apply. Connect the browser extension to finish them.
			</p>
			<a
				href="/extension-auth"
				class="font-mono border-2 border-[var(--color-foreground)] bg-purple-200 px-3 py-1.5 text-xs uppercase tracking-wider text-purple-900 shadow-brutal hover:-translate-y-0.5"
			>
				Connect extension
			</a>
		</div>
	{/if}

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
		onProceed={handleProceed}
		onApply={handleApply}
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

{#if proceedJob}
	{@const job = proceedJob}
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
		role="dialog"
		aria-modal="true"
		aria-labelledby="proceed-title"
	>
		<div class="w-full max-w-lg border-4 border-[var(--color-foreground)] bg-white shadow-brutal">
			<div class="border-b-2 border-[var(--color-foreground)] bg-[var(--color-muted)] px-4 py-3">
				<h2 id="proceed-title" class="font-heading text-lg tracking-tight">Proceed anyway?</h2>
			</div>
			<div class="flex flex-col gap-3 px-4 py-4">
				<p class="text-sm">
					This job was filtered out automatically. Proceeding will generate a tailored CV and
					send it to your review queue.
				</p>
				<div class="border-2 border-[var(--color-foreground)] bg-[var(--color-muted)]/40 px-3 py-2 text-sm">
					<div class="font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
						{(job.job_posting?.title as string | undefined) ?? '—'}
						{#if job.job_posting?.company}· {job.job_posting.company}{/if}
					</div>
					{#if proceedFilter}
						<div class="mt-2 font-mono text-[10px] font-bold uppercase tracking-wider">
							Filter score {proceedFilter.score}/100
						</div>
						{#if proceedFilter.disqualified && proceedFilter.disqualifier_reason}
							<p class="mt-1 text-xs text-red-900">
								<span class="font-bold">Disqualified:</span> {proceedFilter.disqualifier_reason}
							</p>
						{/if}
						{#if proceedFilter.red_flags?.length}
							<ul class="mt-1 list-disc pl-4 text-xs">
								{#each proceedFilter.red_flags as flag}
									<li>{flag}</li>
								{/each}
							</ul>
						{/if}
						{#if proceedFilter.reasoning}
							<p class="mt-1 text-xs text-[var(--color-muted-foreground)]">{proceedFilter.reasoning}</p>
						{/if}
					{/if}
				</div>

				<div>
					<label
						for="proceed-reason"
						class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
					>
						Why was this filtered out by mistake? <span class="normal-case">(optional)</span>
					</label>
					<textarea
						id="proceed-reason"
						bind:value={proceedReason}
						rows={2}
						disabled={proceedSubmitting}
						placeholder="e.g. This is genuinely remote despite the location field. Helps the filter learn."
						class="font-body w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 text-sm placeholder:text-[var(--color-muted-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50"
					></textarea>
				</div>
			</div>
			<div class="flex justify-end gap-2 border-t-2 border-[var(--color-foreground)] px-4 py-3">
				<button
					type="button"
					onclick={cancelProceed}
					disabled={proceedSubmitting}
					class="font-mono border-2 border-[var(--color-foreground)] bg-white px-3 py-1.5 text-xs uppercase tracking-wider hover:bg-[var(--color-muted)] disabled:opacity-50"
				>
					Cancel
				</button>
				<button
					type="button"
					onclick={confirmProceed}
					disabled={proceedSubmitting}
					class="font-mono border-2 border-[var(--color-foreground)] bg-yellow-200 px-3 py-1.5 text-xs uppercase tracking-wider text-yellow-900 shadow-brutal hover:-translate-y-0.5 disabled:opacity-50 disabled:hover:translate-y-0"
				>
					{proceedSubmitting ? 'Starting…' : 'Proceed Anyway'}
				</button>
			</div>
		</div>
	</div>
{/if}

{#if toast}
	<ToastNotification message={toast.message} type={toast.type} onClose={clearToast} />
{/if}
