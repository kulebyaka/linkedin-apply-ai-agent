<script lang="ts">
	import type { JobPosting } from '$lib/types';

	interface Props {
		job: JobPosting;
		applicationUrl: string;
	}

	let { job, applicationUrl }: Props = $props();
</script>

<div class="h-[500px] overflow-y-auto px-6 py-6 sm:px-8">
	<!-- Header -->
	<div class="mb-6">
		<div
			class="mb-2 inline-block border-2 border-[var(--color-foreground)] bg-[var(--color-muted)] px-3 py-1"
		>
			<span class="font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
				{job.company}
			</span>
		</div>
		<h2 class="font-heading text-xl font-bold sm:text-2xl">{job.title}</h2>
	</div>

	<!-- Meta info -->
	<div class="mb-6 flex flex-wrap gap-4 border-b-2 border-[var(--color-muted)] pb-6">
		{#if job.location}
			<div class="flex items-center gap-2 text-sm text-[var(--color-muted-foreground)]">
				<!-- MapPin icon (inline SVG) -->
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="2"
						d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"
					/>
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="2"
						d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"
					/>
				</svg>
				<span class="font-body">{job.location}</span>
			</div>
		{/if}
		{#if job.salary}
			<div class="flex items-center gap-2 text-sm text-[var(--color-muted-foreground)]">
				<!-- DollarSign icon -->
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="2"
						d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
					/>
				</svg>
				<span class="font-body">{job.salary}</span>
			</div>
		{/if}
		{#if job.posted_at}
			<div class="flex items-center gap-2 text-sm text-[var(--color-muted-foreground)]">
				<!-- Clock icon -->
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="2"
						d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
					/>
				</svg>
				<span class="font-body">{job.posted_at}</span>
			</div>
		{/if}
	</div>

	<!-- Description -->
	<div class="mb-6">
		<h3 class="mb-3 font-mono text-sm font-semibold uppercase tracking-wider">Description</h3>
		<div class="whitespace-pre-line font-body text-sm leading-relaxed opacity-90">
			{job.description}
		</div>
	</div>

	<!-- Requirements -->
	{#if job.requirements?.length}
		<div class="mb-6">
			<h3 class="mb-3 font-mono text-sm font-semibold uppercase tracking-wider">Requirements</h3>
			<ul class="space-y-2">
				{#each job.requirements as req}
					<li class="flex items-start gap-2 font-body text-sm opacity-90">
						<span class="mt-1 h-2 w-2 flex-shrink-0 bg-[var(--color-primary)]"></span>
						{req}
					</li>
				{/each}
			</ul>
		</div>
	{/if}

	<!-- Application URL -->
	<div class="border-t-2 border-[var(--color-muted)] pt-6">
		<h3 class="mb-2 font-mono text-sm font-semibold uppercase tracking-wider">Application URL</h3>
		<a
			href={applicationUrl}
			target="_blank"
			rel="noopener noreferrer"
			class="inline-flex items-center gap-2 font-mono text-sm text-[var(--color-primary)] underline underline-offset-4 hover:opacity-80"
		>
			<!-- ExternalLink icon -->
			<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path
					stroke-linecap="round"
					stroke-linejoin="round"
					stroke-width="2"
					d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
				/>
			</svg>
			{applicationUrl}
		</a>
	</div>
</div>
