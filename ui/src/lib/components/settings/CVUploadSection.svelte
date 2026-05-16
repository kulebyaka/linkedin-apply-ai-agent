<script lang="ts">
	import type { User } from '$lib/api/auth';
	import { updateCV } from '$lib/api/settings';
	import { auth } from '$lib/stores/auth.svelte';
	import { page } from '$app/stores';
	import cvTemplate from '$lib/data/cv_template.json';

	let { user }: { user: User } = $props();

	const isOnboarding = $derived($page.url.searchParams.get('onboarding') === '1');

	let cvText = $state(user.master_cv_json ? JSON.stringify(user.master_cv_json, null, 2) : '');
	let jsonValid = $state(true);
	let saving = $state(false);
	let saved = $state(false);
	let error = $state<string | null>(null);
	let fileInput: HTMLInputElement;

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

	function handleDownloadTemplate() {
		const blob = new Blob([JSON.stringify(cvTemplate, null, 2)], { type: 'application/json' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = 'cv_template.json';
		a.click();
		URL.revokeObjectURL(url);
	}

	function handleLoadTemplate() {
		cvText = JSON.stringify(cvTemplate, null, 2);
		jsonValid = true;
		error = null;
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

	{#if isOnboarding && !user.master_cv_json}
		<div class="mb-4 border-2 border-[var(--color-primary)] bg-[var(--color-primary)]/10 px-4 py-3">
			<p class="font-heading mb-1 text-sm font-bold text-[var(--color-foreground)]">
				Welcome — let's set up your master CV
			</p>
			<p class="font-body text-xs text-[var(--color-muted-foreground)]">
				Your CV powers every tailored application. Click <strong>Load template</strong> below to
				start from a working example, edit the fields to match your background, then save.
			</p>
		</div>
	{/if}

	<details class="mb-4 border-2 border-[var(--color-muted)] bg-[var(--color-background)] px-3 py-2">
		<summary class="font-mono cursor-pointer text-xs uppercase tracking-wider text-[var(--color-foreground)]">
			Required fields
		</summary>
		<ul class="font-mono mt-2 list-disc space-y-1 pl-5 text-xs text-[var(--color-muted-foreground)]">
			<li><code>contact.full_name</code>, <code>contact.email</code></li>
			<li><code>summary</code> (1–3 sentences)</li>
			<li><code>experiences[]</code> — each: <code>company</code>, <code>position</code>, <code>start_date</code> (YYYY-MM-DD), <code>description</code></li>
			<li><code>education[]</code> — each: <code>institution</code>, <code>degree</code>, <code>field_of_study</code>, <code>start_date</code></li>
			<li><code>skills[]</code> — each: <code>name</code>, <code>category</code></li>
			<li>Optional: <code>projects</code>, <code>certifications</code>, <code>languages</code>, <code>interests</code></li>
		</ul>
	</details>

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
			placeholder={'{"contact": {"full_name": "..."}, "experiences": [...], "skills": [...]}'}
			disabled={saving}
			rows="12"
			class="font-mono w-full border-2 bg-white px-3 py-2 text-xs leading-relaxed text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50 {jsonValid ? 'border-[var(--color-foreground)]' : 'border-[var(--color-error)]'}"
		></textarea>
		{#if !jsonValid}
			<p class="mt-1 font-mono text-xs text-[var(--color-error)]">Invalid JSON syntax</p>
		{:else if cvText.trim()}
			<p class="mt-1 font-mono text-xs text-[var(--color-success)]">Valid JSON</p>
		{/if}
	</div>

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
			disabled={saving}
			class="border-2 border-[var(--color-foreground)] bg-white px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-[var(--color-foreground)] transition-all duration-200 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
		>
			Upload .json file
		</button>
		<button
			onclick={handleLoadTemplate}
			disabled={saving}
			class="border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-[var(--color-foreground)] transition-all duration-200 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
		>
			Load template
		</button>
		<button
			onclick={handleDownloadTemplate}
			disabled={saving}
			class="border-2 border-[var(--color-foreground)] bg-white px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-[var(--color-foreground)] transition-all duration-200 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
		>
			Download JSON template
		</button>
		<div class="group relative inline-block">
			<button
				disabled
				class="border-2 border-[var(--color-muted)] bg-white px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)] opacity-50 cursor-not-allowed"
			>
				Upload PDF CV
			</button>
			<div
				class="pointer-events-none absolute bottom-full left-0 z-10 mb-2 hidden w-64 border-2 border-[var(--color-foreground)] bg-white px-3 py-2 shadow-brutal group-hover:block"
			>
				<p class="font-mono text-xs text-[var(--color-foreground)]">
					Coming soon — upload your PDF CV and we'll extract and convert it to JSON automatically using AI.
				</p>
			</div>
		</div>
	</div>

	{#if error}
		<div class="mb-4 border-2 border-[var(--color-error)] bg-red-50 px-3 py-2 font-mono text-xs text-[var(--color-error)]">
			{error}
		</div>
	{/if}

	<div class="flex items-center gap-3">
		<button
			onclick={handleSave}
			disabled={saving || !jsonValid || !cvText.trim()}
			class="border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-4 py-2 font-mono text-xs uppercase tracking-wider text-[var(--color-primary-foreground)] shadow-brutal transition-all duration-200 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
		>
			{saving ? 'Saving...' : 'Save CV'}
		</button>
		{#if saved}
			<span class="font-mono text-xs text-[var(--color-success)]">Saved</span>
		{/if}
	</div>
</section>
