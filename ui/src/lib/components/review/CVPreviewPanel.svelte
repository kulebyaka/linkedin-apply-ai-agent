<script lang="ts">
	import { onMount } from 'svelte';
	import { fetchCVHtml, downloadPdf } from '$lib/api/hitl';

	interface Props {
		jobId: string;
	}

	let { jobId }: Props = $props();

	let cvHtml = $state('');
	let isLoading = $state(true);
	let error = $state<string | null>(null);
	let previousJobId = $state('');

	onMount(async () => {
		await loadCVHtml();
	});

	// Reload when jobId changes
	$effect(() => {
		if (jobId && jobId !== previousJobId) {
			previousJobId = jobId;
			loadCVHtml();
		}
	});

	async function loadCVHtml() {
		isLoading = true;
		error = null;
		try {
			cvHtml = await fetchCVHtml(jobId);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load CV';
		} finally {
			isLoading = false;
		}
	}

	function handleDownload() {
		downloadPdf(jobId);
	}
</script>

<div class="flex h-[500px] flex-col">
	<!-- CV Preview Area -->
	<div class="flex-1 overflow-y-auto bg-[var(--color-muted)]/50 p-4">
		{#if isLoading}
			<div class="flex h-full items-center justify-center">
				<div
					class="animate-pulse border-4 border-[var(--color-foreground)] bg-[var(--color-background)] p-8 shadow-brutal"
				>
					<p class="font-mono text-sm uppercase tracking-wider">Loading CV...</p>
				</div>
			</div>
		{:else if error}
			<div class="flex h-full items-center justify-center">
				<div class="border-4 border-[var(--color-destructive)] bg-[var(--color-background)] p-8">
					<p class="font-mono text-sm text-[var(--color-destructive)]">{error}</p>
				</div>
			</div>
		{:else}
			<!-- Render HTML CV (sanitize in production) -->
			<div class="bg-white p-4 shadow-brutal">
				{@html cvHtml}
			</div>
		{/if}
	</div>

	<!-- Download Button -->
	<div class="border-t-2 border-[var(--color-foreground)] bg-[var(--color-background)] p-4">
		<button
			onclick={handleDownload}
			class="flex w-full items-center justify-center gap-2 border-2 border-[var(--color-foreground)]
			       bg-[var(--color-background)] px-6 py-3 font-mono text-sm uppercase tracking-wider
			       shadow-brutal transition-all hover:-translate-y-0.5 hover:shadow-brutal-lg"
		>
			<!-- Download icon -->
			<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path
					stroke-linecap="round"
					stroke-linejoin="round"
					stroke-width="2"
					d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
				/>
			</svg>
			Download Full PDF
		</button>
	</div>
</div>
