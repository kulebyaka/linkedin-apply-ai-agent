<script lang="ts">
	import { onMount } from 'svelte';

	let mounted = $state(false);

	onMount(() => {
		requestAnimationFrame(() => {
			mounted = true;
		});
	});

	const pipelineSteps = [
		{
			num: '01',
			name: 'Job Source',
			tag: 'INPUT',
			description:
				'Jobs arrive via direct URL submission, manual input, or automated hourly LinkedIn scraping when the scheduler is enabled.',
		},
		{
			num: '02',
			name: 'LLM Filter',
			tag: 'AI',
			description:
				'Each job is scored 0–100. Jobs below the reject threshold are discarded; borderline jobs surface warning badges in the review UI.',
		},
		{
			num: '03',
			name: 'CV Composition',
			tag: 'AI',
			description:
				'The AI tailors your master CV to the specific job description, emphasising the most relevant experience and skills.',
		},
		{
			num: '04',
			name: 'PDF Generation',
			tag: 'OUTPUT',
			description:
				'A professional PDF resume is generated from the tailored CV JSON using WeasyPrint + Jinja2 templates.',
		},
		{
			num: '05',
			name: 'HITL Review',
			tag: 'YOU',
			description:
				'Review AI-generated CVs in a Tinder-style interface. Approve, Decline, or ask the AI to Retry with your feedback.',
		},
		{
			num: '06',
			name: 'Application',
			tag: 'AI',
			description:
				'Approved jobs are queued for automated LinkedIn Easy Apply or flagged for manual application.',
		},
		{
			num: '07',
			name: 'History',
			tag: 'LOG',
			description:
				'All decisions are recorded so you can track your full application pipeline at a glance.',
		},
	];

	const features = [
		{
			code: 'LLM',
			title: 'Multi-LLM Support',
			description:
				'Switch between OpenAI, Anthropic, DeepSeek, or Grok via a single environment variable.',
		},
		{
			code: 'AI',
			title: 'Smart Filtering',
			description:
				'Detect hidden disqualifiers — visa requirements, experience minimums — before wasting a tailored CV.',
		},
		{
			code: 'USER',
			title: 'Per-User CVs',
			description:
				'Your master CV is stored securely in your account. Every generated PDF stays in your private directory.',
		},
		{
			code: 'KEY',
			title: 'Keyboard-Driven',
			description:
				'← → navigate, 1 decline, 2 retry, 3 approve. Full keyboard control in the review queue.',
		},
		{
			code: 'SCH',
			title: 'Scheduled Scraping',
			description:
				'Set keywords and filters once. The agent fetches fresh LinkedIn jobs every hour automatically.',
		},
		{
			code: 'RPT',
			title: 'Retry with Feedback',
			description:
				"Not satisfied? Tell the AI exactly what to fix and it regenerates on the spot.",
		},
	];

	const quickStart = [
		{
			num: '01',
			action: 'Upload your master CV',
			detail: 'Paste your full work history as structured JSON',
			href: '/settings',
		},
		{
			num: '02',
			action: 'Configure search preferences',
			detail: 'Keywords, location, remote filter, experience level',
			href: '/settings',
		},
		{
			num: '03',
			action: 'Set filter preferences',
			detail: 'Describe what jobs to reject in plain language',
			href: '/settings',
		},
		{
			num: '04',
			action: 'Submit a job manually',
			detail: 'Or wait for the first scheduled LinkedIn scrape',
			href: '/generate',
		},
		{
			num: '05',
			action: 'Review your first CV',
			detail: 'Approve, decline, or retry with feedback',
			href: '/',
		},
	];

	const tagStyle: Record<string, string> = {
		INPUT: 'bg-sky-100 text-sky-900',
		AI: 'bg-[var(--color-primary)] text-[var(--color-foreground)]',
		OUTPUT: 'bg-emerald-100 text-emerald-900',
		YOU: 'bg-violet-100 text-violet-900',
		LOG: 'bg-[var(--color-muted)] text-[var(--color-muted-foreground)]',
	};
</script>

<svelte:head>
	<title>Welcome — Job Application Agent</title>
</svelte:head>

<div class="grain-texture min-h-screen bg-[var(--color-background)]" class:page-loaded={mounted}>

	<!-- ━━━━━━━━━━━━━━━━ HERO ━━━━━━━━━━━━━━━━ -->
	<section class="section-hero relative overflow-hidden border-b-4 border-[var(--color-foreground)] px-4 py-16 sm:px-8 lg:py-24">

		<!-- Decorative background text -->
		<div
			class="pointer-events-none absolute select-none font-heading text-[22vw] font-bold leading-none tracking-tighter"
			style="top: 1.50em; right: -0.1em; color: var(--color-primary); text-shadow: 10px 10px 0 var(--color-foreground);"
			aria-hidden="true"
		>
			AGENT
		</div>

		<div class="relative mx-auto max-w-4xl">

			<!-- Annotation bar -->
			<div class="mb-8 flex items-center gap-4 section-item" style="--i:0">
				<span class="font-mono text-xs font-bold uppercase tracking-[0.25em] text-[var(--color-primary)]">
					// 01_OVERVIEW
				</span>
				<div class="h-px flex-1 bg-[var(--color-muted)]"></div>
				<div
					class="border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-3 py-1 font-mono text-xs font-bold uppercase shadow-brutal"
				>
					START HERE
				</div>
			</div>

			<!-- Main content row -->
			<div class="flex flex-col gap-10 lg:flex-row lg:items-start lg:gap-16">

				<!-- Heading + body -->
				<div class="flex-1 section-item" style="--i:1">
					<h1 class="font-heading mb-6 text-5xl leading-[1.05] tracking-tight sm:text-6xl lg:text-7xl">
						Your AI<br />
						<span class="relative inline-block">
							<span class="relative z-10">Job Application</span>
							<!-- Amber underline accent -->
							<span
								class="absolute inset-x-0 bottom-1.5 z-0 h-3 bg-[var(--color-primary)]"
								aria-hidden="true"
							></span>
						</span>
						<br />Agent
					</h1>
					<p class="font-body max-w-lg text-lg leading-relaxed text-[var(--color-muted-foreground)] sm:text-xl">
						Automate your job search end-to-end — from LinkedIn scraping to AI-tailored CV
						generation to one-click applications. You stay in control; the agent does the heavy
						lifting.
					</p>

					<div
						class="mt-8 flex items-center gap-2 font-mono text-xs uppercase tracking-[0.15em] text-[var(--color-muted-foreground)]"
					>
						<svg
							class="h-3 w-3 animate-bounce"
							fill="none"
							stroke="currentColor"
							viewBox="0 0 24 24"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M19 9l-7 7-7-7"
							/>
						</svg>
						<span>Scroll to explore</span>
					</div>
				</div>

				<!-- Stat badges -->
				<div class="flex flex-row gap-3 lg:flex-col lg:pt-2 section-item" style="--i:2">
					<div
						class="border-4 border-[var(--color-foreground)] bg-[var(--color-primary)] p-4 text-center shadow-brutal-lg"
					>
						<div class="font-heading text-4xl font-bold leading-none">7</div>
						<div class="mt-1 font-mono text-[10px] uppercase leading-tight tracking-wider">
							pipeline<br />steps
						</div>
					</div>
					<div
						class="border-4 border-[var(--color-foreground)] bg-white p-4 text-center shadow-brutal-lg"
					>
						<div class="font-heading text-4xl font-bold leading-none">4</div>
						<div class="mt-1 font-mono text-[10px] uppercase leading-tight tracking-wider">
							LLM<br />providers
						</div>
					</div>
					<div
						class="border-4 border-[var(--color-foreground)] bg-[var(--color-foreground)] p-4 text-center shadow-brutal-lg"
					>
						<div class="font-heading text-4xl font-bold leading-none text-[var(--color-primary)]">
							∞
						</div>
						<div class="mt-1 font-mono text-[10px] uppercase leading-tight tracking-wider text-white">
							jobs<br />/ hour
						</div>
					</div>
				</div>
			</div>
		</div>
	</section>

	<!-- ━━━━━━━━━━━━━━━━ HOW IT WORKS ━━━━━━━━━━━━━━━━ -->
	<section class="border-b-4 border-[var(--color-foreground)] px-4 py-16 sm:px-8">
		<div class="mx-auto max-w-4xl">

			<!-- Section header -->
			<div class="mb-12 flex items-center gap-4 section-item" style="--i:3">
				<div
					class="border-2 border-[var(--color-foreground)] bg-[var(--color-foreground)] px-3 py-1 font-mono text-xs font-bold uppercase tracking-wider text-[var(--color-primary)]"
				>
					02
				</div>
				<h2 class="font-heading text-2xl font-bold sm:text-3xl">How It Works</h2>
				<div class="h-px flex-1 bg-[var(--color-foreground)]"></div>
			</div>

			<!-- Vertical timeline -->
			<div class="relative ml-2">
				<!-- Connecting line -->
				<div
					class="absolute bottom-4 left-[18px] top-4 w-[3px] bg-[var(--color-primary)]"
					aria-hidden="true"
				></div>

				<div class="space-y-3">
					{#each pipelineSteps as step, i}
						<div
							class="relative flex items-start gap-5 section-item"
							style="--i:{4 + i}"
						>
							<!-- Number badge on the line -->
							<div
								class="relative z-10 flex h-9 w-9 flex-shrink-0 items-center justify-center border-[3px] border-[var(--color-foreground)] bg-[var(--color-primary)] font-heading text-xs font-bold shadow-brutal"
							>
								{step.num}
							</div>

							<!-- Step card -->
							<div
								class="mb-1 flex-1 border-2 border-[var(--color-foreground)] bg-white p-4 shadow-brutal transition-all duration-150 hover:-translate-y-0.5 hover:shadow-brutal-lg"
							>
								<div class="mb-1.5 flex flex-wrap items-center gap-2">
									<span class="font-heading text-sm font-bold">{step.name}</span>
									<span
										class="border border-[var(--color-foreground)] px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase {tagStyle[step.tag] ?? ''}"
									>
										{step.tag}
									</span>
								</div>
								<p class="font-body text-sm leading-relaxed text-[var(--color-muted-foreground)]">
									{step.description}
								</p>
							</div>
						</div>
					{/each}
				</div>
			</div>
		</div>
	</section>

	<!-- ━━━━━━━━━━━━━━━━ FEATURES ━━━━━━━━━━━━━━━━ -->
	<section class="features-section border-b-4 border-[var(--color-foreground)] bg-[var(--color-foreground)] px-4 py-16 sm:px-8">
		<div class="mx-auto max-w-4xl">

			<!-- Section header -->
			<div class="mb-12 flex items-center gap-4 section-item" style="--i:12">
				<div
					class="border-2 border-[var(--color-primary)] bg-[var(--color-primary)] px-3 py-1 font-mono text-xs font-bold uppercase tracking-wider text-[var(--color-foreground)]"
				>
					03
				</div>
				<h2 class="font-heading text-2xl font-bold text-white sm:text-3xl">Feature Highlights</h2>
				<div class="h-px flex-1 bg-white/20"></div>
			</div>

			<!-- Feature cards grid -->
			<div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
				{#each features as feature, i}
					<div
						class="group border-2 border-white/15 bg-white/5 p-5 transition-all duration-150 hover:border-[var(--color-primary)] hover:bg-white/10 section-item"
						style="--i:{13 + i}"
					>
						<div
							class="mb-3 inline-block border-2 border-[var(--color-primary)] bg-[var(--color-primary)] px-2 py-0.5 font-mono text-xs font-bold uppercase text-[var(--color-foreground)]"
						>
							{feature.code}
						</div>
						<h3 class="font-heading mb-2 text-sm font-bold text-white">
							{feature.title}
						</h3>
						<p
							class="font-body text-sm leading-relaxed text-white/55 transition-colors duration-150 group-hover:text-white/80"
						>
							{feature.description}
						</p>
					</div>
				{/each}
			</div>
		</div>
	</section>

	<!-- ━━━━━━━━━━━━━━━━ QUICK START ━━━━━━━━━━━━━━━━ -->
	<section class="border-b-4 border-[var(--color-foreground)] px-4 py-16 sm:px-8">
		<div class="mx-auto max-w-4xl">

			<!-- Section header -->
			<div class="mb-12 flex items-center gap-4 section-item" style="--i:20">
				<div
					class="border-2 border-[var(--color-foreground)] bg-[var(--color-foreground)] px-3 py-1 font-mono text-xs font-bold uppercase tracking-wider text-[var(--color-primary)]"
				>
					04
				</div>
				<h2 class="font-heading text-2xl font-bold sm:text-3xl">Quick Start</h2>
				<div class="h-px flex-1 bg-[var(--color-foreground)]"></div>
			</div>

			<!-- Step links -->
			<div class="space-y-2">
				{#each quickStart as step, i}
					<a
						href={step.href}
						class="group flex items-center gap-4 border-2 border-[var(--color-foreground)] bg-white p-4 shadow-brutal transition-all duration-150 hover:-translate-y-0.5 hover:bg-[var(--color-primary)] hover:shadow-brutal-lg section-item"
						style="--i:{21 + i}"
					>
						<!-- Number -->
						<div
							class="flex-shrink-0 border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-2 py-1 font-heading text-sm font-bold shadow-brutal transition-colors duration-150 group-hover:bg-[var(--color-foreground)] group-hover:text-[var(--color-primary)]"
						>
							{step.num}
						</div>

						<!-- Content -->
						<div class="min-w-0 flex-1">
							<div class="font-heading text-sm font-bold">{step.action}</div>
							<div
								class="font-body text-xs text-[var(--color-muted-foreground)] transition-colors duration-150 group-hover:text-[var(--color-foreground)]/70"
							>
								{step.detail}
							</div>
						</div>

						<!-- Arrow -->
						<svg
							class="h-4 w-4 flex-shrink-0 text-[var(--color-muted-foreground)] transition-all duration-150 group-hover:translate-x-1 group-hover:text-[var(--color-foreground)]"
							fill="none"
							stroke="currentColor"
							viewBox="0 0 24 24"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M9 5l7 7-7 7"
							/>
						</svg>
					</a>
				{/each}
			</div>
		</div>
	</section>

	<!-- ━━━━━━━━━━━━━━━━ CTA ━━━━━━━━━━━━━━━━ -->
	<section class="bg-[var(--color-primary)] px-4 py-16 sm:px-8">
		<div class="mx-auto max-w-4xl section-item" style="--i:27">
			<div
				class="flex flex-col items-start gap-6 sm:flex-row sm:items-center sm:justify-between"
			>
				<div>
					<p
						class="mb-1 font-mono text-xs font-bold uppercase tracking-[0.2em] text-[var(--color-foreground)]/60"
					>
						// READY?
					</p>
					<h2 class="font-heading text-2xl font-bold text-[var(--color-foreground)] sm:text-3xl">
						Start with Settings
					</h2>
					<p class="font-body mt-1 text-sm text-[var(--color-foreground)]/70">
						Upload your CV and configure your search preferences to get going.
					</p>
				</div>
				<a
					href="/settings"
					class="flex-shrink-0 border-4 border-[var(--color-foreground)] bg-[var(--color-foreground)] px-8 py-4 font-mono text-sm font-bold uppercase tracking-wider text-[var(--color-primary)] shadow-brutal-xl transition-all duration-150 hover:-translate-y-1 hover:shadow-[10px_10px_0_rgba(0,0,0,0.4)]"
				>
					Go to Settings →
				</a>
			</div>
		</div>
	</section>

</div>

<style>
	@keyframes slideUp {
		from {
			opacity: 0;
			transform: translateY(20px);
		}
		to {
			opacity: 1;
			transform: translateY(0);
		}
	}

	.features-section {
		background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='80'%3E%3Ctext x='60' y='44' text-anchor='middle' transform='rotate(-30 60 44)' font-family='monospace' font-size='12' font-weight='700' fill='%23f59e0b' fill-opacity='0.18' letter-spacing='2'%3Eagent%3C/text%3E%3C/svg%3E");
		background-repeat: repeat;
	}

	.section-item {
		opacity: 0;
		transform: translateY(20px);
	}

	.page-loaded .section-item {
		animation: slideUp 0.45s ease both;
		/* Use CSS custom property --i for stagger delay */
		animation-delay: calc(var(--i, 0) * 55ms);
	}
</style>
