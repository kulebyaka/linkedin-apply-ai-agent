<script lang="ts">
	import type { User } from '$lib/api/auth';
	import { updateCV } from '$lib/api/settings';
	import { auth } from '$lib/stores/auth.svelte';

	let { user }: { user: User } = $props();

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
		const name = contact?.name ?? 'Unknown';
		const experiences = Array.isArray(cv.experience) ? cv.experience.length : 0;
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
			placeholder={'{"contact": {"name": "..."}, "experience": [...], "skills": [...]}'}
			disabled={saving}
			rows="12"
			class="font-mono w-full border-2 bg-white px-3 py-2 text-xs leading-relaxed text-[var(--color-foreground)] placeholder:text-[var(--color-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50 {jsonValid ? 'border-[var(--color-foreground)]' : 'border-[var(--color-error)]'}"
		></textarea>
		{#if !jsonValid}
			<p class="mt-1 font-mono text-xs text-[var(--color-error)]">Invalid JSON syntax</p>
		{:else if cvText.trim()}
			<p class="mt-1 font-mono text-xs text-[var(--color-success)]">Valid JSON</p>
		{/if}
	</div>

	<div class="mb-4">
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
