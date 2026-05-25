<script lang="ts">
	import { onDestroy } from 'svelte';
	import type { User } from '$lib/api/auth';
	import {
		extractCVFromPDF,
		getCVExtractionStatus,
		updateCV,
	} from '$lib/api/settings';
	import { auth } from '$lib/stores/auth.svelte';
	import ToastNotification from '$lib/components/ToastNotification.svelte';

	let { user }: { user: User } = $props();

	let cvText = $state(user.master_cv_json ? JSON.stringify(user.master_cv_json, null, 2) : '');
	let jsonValid = $state(true);
	let saving = $state(false);
	let saved = $state(false);
	let error = $state<string | null>(null);
	let fileInput: HTMLInputElement;
	let pdfInput: HTMLInputElement;

	let extractionStatus = $state<string | null>(null);
	let validationErrors = $state<string[]>([]);
	let pollTimer: ReturnType<typeof setTimeout> | null = null;
	const extracting = $derived(extractionStatus !== null);

	let toastMessage = $state<string | null>(null);
	let toastType = $state<'success' | 'error' | 'info'>('success');

	onDestroy(() => {
		if (pollTimer !== null) {
			clearTimeout(pollTimer);
			pollTimer = null;
		}
	});

	const cvSummary = $derived.by(() => {
		if (!user.master_cv_json) return null;
		const cv = user.master_cv_json as Record<string, unknown>;
		const contact = cv.contact as Record<string, unknown> | undefined;
		const name = (contact?.full_name ?? contact?.name ?? 'Unknown') as string;
		const experiences = Array.isArray(cv.experiences) ? cv.experiences.length : 0;
		const skills = Array.isArray(cv.skills) ? cv.skills.length : 0;
		return { name, experiences, skills };
	});

	function validateJson(text: string): boolean {
		if (!text.trim()) return true;
		try {
			JSON.parse(text);
			return true;
		} catch {
			return false;
		}
	}

	function handleInput() {
		jsonValid = validateJson(cvText);
	}

	function handleFileUpload(e: Event) {
		const target = e.target as HTMLInputElement;
		const file = target.files?.[0];
		if (!file) return;

		const reader = new FileReader();
		reader.onload = (evt) => {
			const content = evt.target?.result as string;
			cvText = content;
			jsonValid = validateJson(content);
		};
		reader.readAsText(file);
	}

	async function pollExtraction(extractionId: string) {
		try {
			const status = await getCVExtractionStatus(extractionId);

			if (status.status === 'pending' || status.status === 'running') {
				if (extractionStatus !== status.status) extractionStatus = status.status;
				pollTimer = setTimeout(() => pollExtraction(extractionId), 2000);
				return;
			}

			pollTimer = null;

			if (status.status === 'failed') {
				error = status.error_message || 'Extraction failed';
				extractionStatus = null;
				toastType = 'error';
				toastMessage = error;
				return;
			}

			if (status.result_json) {
				cvText = JSON.stringify(status.result_json, null, 2);
				jsonValid = validateJson(cvText);
			}
			validationErrors = status.validation_errors || [];
			extractionStatus = null;
			error = null;
			toastType = 'success';
			toastMessage =
				validationErrors.length > 0
					? `CV extracted with ${validationErrors.length} validation issue${validationErrors.length === 1 ? '' : 's'} — review below.`
					: 'CV extracted from PDF. Review and click Save.';
		} catch (err) {
			pollTimer = null;
			extractionStatus = null;
			error = err instanceof Error ? err.message : 'Failed to poll extraction';
		}
	}

	async function handlePdfUpload(e: Event) {
		const target = e.target as HTMLInputElement;
		const file = target.files?.[0];
		// allow re-selecting the same file later
		target.value = '';
		if (!file) return;

		error = null;
		validationErrors = [];
		extractionStatus = 'uploading';
		try {
			const started = await extractCVFromPDF(file);
			extractionStatus = started.status;
			pollExtraction(started.extraction_id);
		} catch (err) {
			extractionStatus = null;
			error = err instanceof Error ? err.message : 'Failed to upload PDF';
		}
	}

	const CV_TEMPLATE = {
		contact: {
			full_name: 'Jane Smith',
			email: 'jane.smith@example.com',
			phone: '+1 555 000 0000',
			location: 'San Francisco, CA',
			linkedin_url: 'https://linkedin.com/in/janesmith',
			github_url: 'https://github.com/janesmith',
			portfolio_url: null
		},
		summary:
			'Experienced software engineer with 8+ years building scalable distributed systems. Passionate about developer tooling, clean architecture, and mentoring.',
		experiences: [
			{
				company: 'Acme Corp',
				position: 'Senior Software Engineer',
				start_date: '2020-03-01',
				end_date: null,
				is_current: true,
				location: 'San Francisco, CA',
				description:
					'Lead engineer on the platform team responsible for core infrastructure serving 10M+ users.',
				achievements: [
					'Reduced API latency by 40% through caching and query optimization',
					'Mentored 3 junior engineers, improving team velocity by 20%'
				],
				technologies: ['Python', 'FastAPI', 'PostgreSQL', 'Redis', 'Kubernetes'],
				projects: [
					{
						name: 'Data Pipeline Redesign',
						role: 'Tech Lead',
						description: 'Redesigned the ETL pipeline processing 10M events/day',
						achievements: ['Improved throughput by 3x', 'Reduced cost by 50%'],
						technologies: ['Apache Kafka', 'Spark', 'Python'],
						duration: '2021-2022'
					}
				],
				company_context: {
					industry: 'SaaS / FinTech',
					size: '500-1000',
					notable_clients: ['Fortune 500 Co', 'Global Bank']
				}
			}
		],
		education: [
			{
				institution: 'University of California, Berkeley',
				degree: 'Bachelor of Science',
				field_of_study: 'Computer Science',
				start_date: '2012-09-01',
				end_date: '2016-05-31',
				gpa: '3.8',
				achievements: ["Dean's List", 'Hackathon Winner 2015']
			}
		],
		skills: [
			{
				name: 'Python',
				category: 'Programming Languages',
				proficiency: 'Expert',
				years_of_experience: '8+',
				use_cases: ['Backend APIs', 'Data pipelines', 'ML scripting']
			},
			{
				name: 'TypeScript',
				category: 'Programming Languages',
				proficiency: 'Intermediate',
				years_of_experience: '4',
				use_cases: ['Frontend (React/Svelte)', 'Node.js services']
			}
		],
		projects: [
			{
				name: 'Open Source CLI Tool',
				description: 'A CLI tool for automating developer workflows',
				url: 'https://github.com/janesmith/cli-tool',
				technologies: ['Python', 'Click', 'Rich'],
				achievements: ['2000+ GitHub stars', 'Used by 500+ developers'],
				status: 'active',
				last_updated: '2024-01-15',
				role: 'Creator & Maintainer',
				architecture: ['CLI', 'Plugin system'],
				visibility: 'public'
			}
		],
		certifications: [
			{
				name: 'AWS Solutions Architect – Associate',
				issuer: 'Amazon',
				date: '2022-06',
				description: 'Cloud architecture and AWS service design',
				topics: ['EC2', 'S3', 'VPC', 'Lambda', 'RDS']
			}
		],
		languages: [
			{ language: 'English', level: 'Native' },
			{ language: 'Spanish', level: 'Professional Working Proficiency' }
		],
		interests: {
			technical: ['Distributed systems', 'Open source', 'Developer tooling'],
			sports: ['Rock climbing', 'Cycling'],
			other: ['Photography', 'Coffee brewing']
		}
	};

	function handleDownloadTemplate() {
		const blob = new Blob([JSON.stringify(CV_TEMPLATE, null, 2)], { type: 'application/json' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = 'cv_template.json';
		a.click();
		URL.revokeObjectURL(url);
	}

	async function handleSave() {
		if (!cvText.trim()) {
			error = 'CV JSON cannot be empty';
			return;
		}
		if (!jsonValid) {
			error = 'Fix JSON syntax errors before saving';
			return;
		}

		saving = true;
		error = null;
		saved = false;

		try {
			const parsed = JSON.parse(cvText);
			const updated = await updateCV(parsed);
			auth.setUser(updated);
			saved = true;
			setTimeout(() => (saved = false), 2000);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to save CV';
		} finally {
			saving = false;
		}
	}
</script>

<section class="border-4 border-[var(--color-foreground)] bg-white p-6 shadow-brutal">
	<h2 class="font-heading mb-4 text-lg tracking-tight">Master CV</h2>

	{#if cvSummary}
		<div class="mb-4 border-2 border-[var(--color-muted)] bg-[var(--color-background)] px-3 py-2">
			<p class="font-mono text-xs text-[var(--color-muted-foreground)]">
				Current CV: <span class="font-bold text-[var(--color-foreground)]">{cvSummary.name}</span>
				&mdash; {cvSummary.experiences} experience{cvSummary.experiences !== 1 ? 's' : ''},
				{cvSummary.skills} skill{cvSummary.skills !== 1 ? 's' : ''}
			</p>
		</div>
	{/if}

	<div class="mb-3">
		<label
			for="cvJson"
			class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
		>
			CV JSON
		</label>
		<textarea
			id="cvJson"
			bind:value={cvText}
			oninput={handleInput}
			placeholder={'{"contact": {"full_name": "..."}, "experience": [...], "skills": [...]}'}
			disabled={saving || extracting}
			rows="12"
			class="font-mono w-full border-2 bg-white px-3 py-2 text-xs leading-relaxed text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50 {jsonValid ? 'border-[var(--color-foreground)]' : 'border-[var(--color-error)]'}"
		></textarea>
		{#if !jsonValid}
			<p class="mt-1 font-mono text-xs text-[var(--color-error)]">Invalid JSON syntax</p>
		{:else if cvText.trim()}
			<p class="mt-1 font-mono text-xs text-[var(--color-success)]">Valid JSON</p>
		{/if}
	</div>

	{#if extracting}
		{@const statusLabel = extractionStatus === 'uploading'
			? 'Uploading PDF…'
			: extractionStatus === 'pending'
				? 'Queued — waiting for worker…'
				: extractionStatus === 'running'
					? 'Extracting CV with LLM (this can take 30–60s)…'
					: 'Working…'}
		<div class="mb-4 border-2 border-[var(--color-foreground)] bg-[var(--color-background)] px-3 py-3">
			<div class="mb-2 flex items-center gap-2 font-mono text-xs uppercase tracking-wider text-[var(--color-foreground)]">
				<svg
					class="-ml-0.5 h-4 w-4 animate-spin text-[var(--color-foreground)]"
					xmlns="http://www.w3.org/2000/svg"
					fill="none"
					viewBox="0 0 24 24"
				>
					<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
					<path
						class="opacity-75"
						fill="currentColor"
						d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
					></path>
				</svg>
				<span>{statusLabel}</span>
			</div>
			<div class="h-2 w-full overflow-hidden border-2 border-[var(--color-foreground)] bg-white">
				<div class="cv-progress-bar h-full bg-[var(--color-primary)]"></div>
			</div>
		</div>
	{/if}

	<div class="mb-4 flex flex-wrap items-center gap-2">
		<input
			bind:this={fileInput}
			type="file"
			accept=".json"
			onchange={handleFileUpload}
			class="hidden"
		/>
		<button
			onclick={() => fileInput.click()}
			disabled={saving || extracting}
			class="border-2 border-[var(--color-foreground)] bg-white px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-[var(--color-foreground)] transition-all duration-200 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
		>
			Upload .json file
		</button>
		<button
			onclick={handleDownloadTemplate}
			disabled={saving || extracting}
			class="border-2 border-[var(--color-foreground)] bg-white px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-[var(--color-foreground)] transition-all duration-200 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
		>
			Download JSON template
		</button>
		<input
			bind:this={pdfInput}
			type="file"
			accept="application/pdf,.pdf"
			onchange={handlePdfUpload}
			class="hidden"
		/>
		<button
			onclick={() => pdfInput.click()}
			disabled={saving || extracting}
			class="border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-[var(--color-primary-foreground)] transition-all duration-200 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
		>
			{#if extracting}
				Extracting…{extractionStatus ? ` (${extractionStatus})` : ''}
			{:else}
				Upload PDF CV
			{/if}
		</button>
	</div>

	{#if validationErrors.length > 0}
		<div class="mb-4 border-2 border-[var(--color-warning,#b45309)] bg-amber-50 px-3 py-2 font-mono text-xs text-[var(--color-foreground)]">
			<p class="mb-1 font-bold uppercase tracking-wider">
				Extracted JSON has {validationErrors.length} validation issue{validationErrors.length === 1 ? '' : 's'} — fix in editor before saving:
			</p>
			<ul class="list-disc pl-5">
				{#each validationErrors as ve}
					<li>{ve}</li>
				{/each}
			</ul>
		</div>
	{/if}

	{#if error}
		<div class="mb-4 border-2 border-[var(--color-error)] bg-red-50 px-3 py-2 font-mono text-xs text-[var(--color-error)]">
			{error}
		</div>
	{/if}

	<div class="flex items-center gap-3">
		<button
			onclick={handleSave}
			disabled={saving || extracting || !jsonValid || !cvText.trim()}
			class="border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-4 py-2 font-mono text-xs uppercase tracking-wider text-[var(--color-primary-foreground)] shadow-brutal transition-all duration-200 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
		>
			{saving ? 'Saving...' : 'Save CV'}
		</button>
		{#if saved}
			<span class="font-mono text-xs text-[var(--color-success)]">Saved</span>
		{/if}
	</div>
</section>

{#if toastMessage}
	<ToastNotification
		message={toastMessage}
		type={toastType}
		onClose={() => (toastMessage = null)}
	/>
{/if}

<style>
	.cv-progress-bar {
		width: 40%;
		animation: cv-progress-slide 1.4s ease-in-out infinite;
	}

	@keyframes cv-progress-slide {
		0% {
			margin-left: -40%;
		}
		100% {
			margin-left: 100%;
		}
	}
</style>
