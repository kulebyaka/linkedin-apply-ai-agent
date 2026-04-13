<script lang="ts">
	import type { PendingApproval, FilterResult } from '$lib/types';
	import PanelSwitcher from './PanelSwitcher.svelte';
	import JobDescriptionPanel from './JobDescriptionPanel.svelte';
	import CVPreviewPanel from './CVPreviewPanel.svelte';

	interface Props {
		job: PendingApproval;
	}

	let { job }: Props = $props();

	let currentPanel = $state<'job' | 'cv'>('job');

	function handlePanelChange(panel: 'job' | 'cv') {
		currentPanel = panel;
	}

	function getScoreBadgeClass(result: FilterResult): string {
		if (result.disqualified || result.score < job.reject_threshold) {
			return 'border-2 border-red-500 bg-red-100 text-red-800';
		} else if (result.score < job.warning_threshold) {
			return 'border-2 border-yellow-500 bg-yellow-100 text-yellow-800';
		} else {
			return 'border-2 border-green-500 bg-green-100 text-green-800';
		}
	}

	function getScoreLabel(result: FilterResult): string {
		if (result.disqualified || result.score < job.reject_threshold) {
			return 'Low Match';
		} else if (result.score < job.warning_threshold) {
			return 'Moderate';
		} else {
			return 'Good Match';
		}
	}
</script>

<div
	class="border-4 border-[var(--color-foreground)] bg-[var(--color-background)] shadow-brutal-lg"
>
	<!-- Panel Toggle -->
	<PanelSwitcher {currentPanel} onPanelChange={handlePanelChange} />

	<!-- Panel Content -->
	<div class="border-t-2 border-[var(--color-foreground)]">
		{#if currentPanel === 'job'}
			<JobDescriptionPanel job={job.job_posting} applicationUrl={job.application_url} />
		{:else}
			<CVPreviewPanel jobId={job.job_id} />
		{/if}
	</div>

	<!-- Metadata Footer -->
	<div
		class="border-t-2 border-[var(--color-foreground)] bg-[var(--color-muted)]/50 px-6 py-3"
	>
		<div class="flex flex-wrap items-center gap-3">
			<span class="font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
				{job.job_posting.company}
			</span>
			<span class="h-1 w-1 bg-[var(--color-muted-foreground)]"></span>
			<span class="font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
				{job.job_posting.title}
			</span>
			<span class="h-1 w-1 bg-[var(--color-muted-foreground)]"></span>
			<span class="font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
				Attempts: {job.attempt_count}
			</span>
			<span class="h-1 w-1 bg-[var(--color-muted-foreground)]"></span>
			<span class="font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
				Source: {job.source}
			</span>
			{#if job.filter_result}
				<span class="h-1 w-1 bg-[var(--color-muted-foreground)]"></span>
				<span
					class="font-mono text-xs uppercase tracking-wider px-2 py-0.5 {getScoreBadgeClass(job.filter_result)}"
				>
					Filter: {job.filter_result.score}/100 — {getScoreLabel(job.filter_result)}
				</span>
			{/if}
		</div>
		{#if job.filter_result && job.filter_result.red_flags.length > 0}
			<div class="mt-2 flex flex-wrap gap-1.5">
				{#each job.filter_result.red_flags as flag}
					<span
						class="border border-red-400 bg-red-50 px-2 py-0.5 font-mono text-xs text-red-700"
					>
						{flag}
					</span>
				{/each}
			</div>
		{/if}
	</div>
</div>
