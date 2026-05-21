<script lang="ts">
	import type { AdminJobRecord } from '$lib/api/admin';

	interface Props {
		jobs: AdminJobRecord[];
		userEmailById: Record<string, string>;
		selected: Set<string>;
		onToggleSelect: (jobId: string) => void;
		onToggleSelectAll: () => void;
		onRetry: (jobId: string) => void;
		onDelete: (jobId: string) => void;
		onView: (jobId: string) => void;
		loading?: boolean;
	}

	let {
		jobs,
		userEmailById,
		selected,
		onToggleSelect,
		onToggleSelectAll,
		onRetry,
		onDelete,
		onView,
		loading = false,
	}: Props = $props();

	const allSelected = $derived(jobs.length > 0 && jobs.every((j) => selected.has(j.job_id)));

	function formatDate(iso: string | null | undefined): string {
		if (!iso) return '—';
		try {
			const d = new Date(iso);
			return d.toLocaleString();
		} catch {
			return iso;
		}
	}

	function statusBadgeClass(status: string): string {
		const base =
			'inline-block border-2 border-[var(--color-foreground)] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider';
		const colorMap: Record<string, string> = {
			queued: 'bg-white text-[var(--color-foreground)]',
			processing: 'bg-blue-100 text-blue-900',
			cv_ready: 'bg-green-100 text-green-900',
			pending_review: 'bg-yellow-100 text-yellow-900',
			pending: 'bg-yellow-100 text-yellow-900',
			approved: 'bg-emerald-200 text-emerald-900',
			declined: 'bg-zinc-200 text-zinc-700',
			retrying: 'bg-orange-100 text-orange-900',
			applying: 'bg-indigo-100 text-indigo-900',
			applied: 'bg-emerald-300 text-emerald-900',
			failed: 'bg-red-200 text-red-900',
			filtered_out: 'bg-zinc-200 text-zinc-700',
			completed: 'bg-emerald-200 text-emerald-900',
		};
		return `${base} ${colorMap[status] ?? 'bg-white text-[var(--color-foreground)]'}`;
	}

	function userEmail(userId: string | null | undefined): string {
		if (!userId) return '—';
		return userEmailById[userId] ?? userId.slice(0, 8);
	}

	function jobTitle(j: AdminJobRecord): string {
		return (j.job_posting?.title as string | undefined) ?? '—';
	}

	function jobCompany(j: AdminJobRecord): string {
		return (j.job_posting?.company as string | undefined) ?? '—';
	}

	function hasError(j: AdminJobRecord): boolean {
		return Boolean(j.error_message || j.last_scrape_error);
	}
</script>

<div class="overflow-x-auto border-4 border-[var(--color-foreground)] bg-white shadow-brutal">
	<table class="w-full border-collapse text-sm">
		<thead class="border-b-2 border-[var(--color-foreground)] bg-[var(--color-muted)]">
			<tr>
				<th class="px-3 py-2 text-left">
					<input
						type="checkbox"
						checked={allSelected}
						onchange={onToggleSelectAll}
						aria-label="Select all jobs"
						class="h-4 w-4 border-2 border-[var(--color-foreground)]"
					/>
				</th>
				<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">Created</th>
				<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">User</th>
				<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">Status</th>
				<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">Source</th>
				<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">Title</th>
				<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">Company</th>
				<th class="font-mono px-3 py-2 text-center text-xs uppercase tracking-wider">Err</th>
				<th class="font-mono px-3 py-2 text-right text-xs uppercase tracking-wider">Actions</th>
			</tr>
		</thead>
		<tbody>
			{#if loading && jobs.length === 0}
				<tr>
					<td colspan="9" class="px-3 py-8 text-center font-mono text-xs text-[var(--color-muted-foreground)]">
						Loading…
					</td>
				</tr>
			{:else if jobs.length === 0}
				<tr>
					<td colspan="9" class="px-3 py-8 text-center font-mono text-xs text-[var(--color-muted-foreground)]">
						No jobs match the current filters.
					</td>
				</tr>
			{:else}
				{#each jobs as j (j.job_id)}
					<tr class="border-b border-[var(--color-muted)] hover:bg-[var(--color-muted)]/40">
						<td class="px-3 py-2">
							<input
								type="checkbox"
								checked={selected.has(j.job_id)}
								onchange={() => onToggleSelect(j.job_id)}
								aria-label="Select job {j.job_id}"
								class="h-4 w-4 border-2 border-[var(--color-foreground)]"
							/>
						</td>
						<td class="px-3 py-2 font-mono text-xs">{formatDate(j.created_at)}</td>
						<td class="px-3 py-2 font-mono text-xs">{userEmail(j.user_id)}</td>
						<td class="px-3 py-2">
							<span class={statusBadgeClass(j.status)}>{j.status}</span>
						</td>
						<td class="px-3 py-2 font-mono text-xs">{j.source}</td>
						<td class="px-3 py-2 text-sm">{jobTitle(j)}</td>
						<td class="px-3 py-2 text-sm">{jobCompany(j)}</td>
						<td class="px-3 py-2 text-center">
							{#if hasError(j)}
								<span
									class="inline-block border-2 border-[var(--color-error,red)] bg-red-100 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-red-900"
									title={j.error_message ?? j.last_scrape_error ?? ''}>!</span
								>
							{:else}
								<span class="font-mono text-xs text-[var(--color-muted-foreground)]">—</span>
							{/if}
						</td>
						<td class="px-3 py-2 text-right">
							<div class="inline-flex gap-1">
								<button
									type="button"
									onclick={() => onView(j.job_id)}
									class="font-mono border-2 border-[var(--color-foreground)] bg-white px-2 py-1 text-[10px] uppercase tracking-wider hover:bg-[var(--color-muted)]"
								>
									View
								</button>
								{#if j.status === 'failed'}
									<button
										type="button"
										onclick={() => onRetry(j.job_id)}
										class="font-mono border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-2 py-1 text-[10px] uppercase tracking-wider text-[var(--color-primary-foreground)] hover:-translate-y-0.5"
									>
										Retry
									</button>
								{/if}
								<button
									type="button"
									onclick={() => onDelete(j.job_id)}
									class="font-mono border-2 border-[var(--color-foreground)] bg-red-100 px-2 py-1 text-[10px] uppercase tracking-wider text-red-900 hover:bg-red-200"
								>
									Delete
								</button>
							</div>
						</td>
					</tr>
				{/each}
			{/if}
		</tbody>
	</table>
</div>
