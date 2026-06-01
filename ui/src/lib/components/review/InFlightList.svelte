<script lang="ts">
	import type { PendingApproval } from '$lib/types';

	type Props = { jobs: PendingApproval[] };
	let { jobs }: Props = $props();

	const STEP_LABELS: Record<string, string> = {
		extracting: 'Scraping',
		job_extracted: 'Scraped',
		filtering: 'Filtering',
		job_filtered: 'Filtered',
		composing_cv: 'Composing CV',
		cv_composed: 'CV composed',
		generating_pdf: 'Generating PDF',
		pdf_generated: 'PDF ready',
		saving: 'Saving'
	};

	const STATUS_LABELS: Record<string, string> = {
		queued: 'Queued',
		processing: 'Processing',
		retrying: 'Retrying'
	};

	function badgeFor(job: PendingApproval): string {
		if (job.workflow_step && STEP_LABELS[job.workflow_step]) {
			return STEP_LABELS[job.workflow_step];
		}
		return STATUS_LABELS[job.status] ?? job.status;
	}
</script>

{#if jobs.length > 0}
	<section class="mt-8">
		<h2 class="font-heading mb-3 text-lg font-semibold">In flight ({jobs.length})</h2>
		<ul class="space-y-2">
			{#each jobs as job (job.job_id)}
				<li
					class="flex items-center justify-between gap-3 border-2 border-[var(--color-foreground)] bg-[var(--color-background)] p-3 shadow-brutal"
				>
					<div class="min-w-0 flex-1">
						<p class="truncate font-body text-sm font-semibold">
							{job.job_posting?.title ?? '(untitled)'}
						</p>
						<p class="truncate font-mono text-xs text-[var(--color-muted-foreground)]">
							{job.job_posting?.company ?? ''}
						</p>
					</div>
					<div
						class="flex shrink-0 items-center gap-2 border-2 border-[var(--color-foreground)] bg-[var(--color-accent)] px-2 py-1"
						title="status: {job.status}"
					>
						<span
							class="inline-block h-2 w-2 animate-pulse rounded-full bg-[var(--color-foreground)]"
						></span>
						<span class="font-mono text-xs font-semibold">{badgeFor(job)}</span>
					</div>
				</li>
			{/each}
		</ul>
	</section>
{/if}
