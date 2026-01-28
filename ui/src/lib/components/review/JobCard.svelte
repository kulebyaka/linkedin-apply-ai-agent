<script lang="ts">
	import type { PendingApproval } from '$lib/types';
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
		class="flex flex-wrap items-center gap-3 border-t-2 border-[var(--color-foreground)] bg-[var(--color-muted)]/50 px-6 py-3"
	>
		<span class="font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
			{job.job_posting.company}
		</span>
		<span class="h-1 w-1 bg-[var(--color-muted-foreground)]"></span>
		<span class="font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
			{job.job_posting.title}
		</span>
		<span class="h-1 w-1 bg-[var(--color-muted-foreground)]"></span>
		<span class="font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
			Retry: {job.retry_count}
		</span>
		<span class="h-1 w-1 bg-[var(--color-muted-foreground)]"></span>
		<span class="font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
			Source: {job.source}
		</span>
	</div>
</div>
