<script lang="ts">
	import type { UserSearchPreferences } from '$lib/api/auth';
	import { updateSearchPreferences } from '$lib/api/settings';
	import { auth } from '$lib/stores/auth.svelte';

	let { prefs: initialPrefs }: { prefs: UserSearchPreferences } = $props();

	let keywords = $state(initialPrefs.keywords);
	let location = $state(initialPrefs.location);
	let remoteFilter = $state(initialPrefs.remote_filter ?? '');
	let datePosted = $state(initialPrefs.date_posted ?? '');
	let experienceLevel = $state<string[]>(initialPrefs.experience_level ?? []);
	let jobType = $state<string[]>(initialPrefs.job_type ?? []);
	let easyApplyOnly = $state(initialPrefs.easy_apply_only);
	let maxJobs = $state(initialPrefs.max_jobs);
	let saving = $state(false);
	let saved = $state(false);
	let error = $state<string | null>(null);

	const experienceLevelOptions = [
		{ value: 'internship', label: 'Internship' },
		{ value: 'entry', label: 'Entry Level' },
		{ value: 'associate', label: 'Associate' },
		{ value: 'mid-senior', label: 'Mid-Senior' },
		{ value: 'director', label: 'Director' },
		{ value: 'executive', label: 'Executive' },
	];

	const jobTypeOptions = [
		{ value: 'full-time', label: 'Full-time' },
		{ value: 'part-time', label: 'Part-time' },
		{ value: 'contract', label: 'Contract' },
		{ value: 'temporary', label: 'Temporary' },
		{ value: 'internship', label: 'Internship' },
		{ value: 'volunteer', label: 'Volunteer' },
	];

	function toggleItem(arr: string[], value: string): string[] {
		return arr.includes(value) ? arr.filter((v) => v !== value) : [...arr, value];
	}

	async function handleSave() {
		saving = true;
		error = null;
		saved = false;

		const payload: UserSearchPreferences = {
			keywords,
			location,
			remote_filter: remoteFilter || null,
			date_posted: datePosted || null,
			experience_level: experienceLevel.length > 0 ? experienceLevel : null,
			job_type: jobType.length > 0 ? jobType : null,
			easy_apply_only: easyApplyOnly,
			max_jobs: maxJobs,
		};

		try {
			const updated = await updateSearchPreferences(payload);
			auth.setUser(updated);
			saved = true;
			setTimeout(() => (saved = false), 2000);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to save search preferences';
		} finally {
			saving = false;
		}
	}
</script>

<section class="border-4 border-[var(--color-foreground)] bg-white p-6 shadow-brutal">
	<h2 class="font-heading mb-4 text-lg tracking-tight">LinkedIn Search Preferences</h2>

	<div class="grid gap-4 sm:grid-cols-2">
		<div>
			<label
				for="keywords"
				class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
			>
				Keywords
			</label>
			<input
				id="keywords"
				type="text"
				bind:value={keywords}
				placeholder="e.g. Python Developer"
				disabled={saving}
				class="font-body w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 text-sm text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50"
			/>
		</div>

		<div>
			<label
				for="location"
				class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
			>
				Location
			</label>
			<input
				id="location"
				type="text"
				bind:value={location}
				placeholder="e.g. San Francisco, CA"
				disabled={saving}
				class="font-body w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 text-sm text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50"
			/>
		</div>

		<div>
			<label
				for="remoteFilter"
				class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
			>
				Remote Filter
			</label>
			<select
				id="remoteFilter"
				bind:value={remoteFilter}
				disabled={saving}
				class="font-body w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 text-sm text-[var(--color-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50"
			>
				<option value="">Any</option>
				<option value="remote">Remote</option>
				<option value="on-site">On-site</option>
				<option value="hybrid">Hybrid</option>
			</select>
		</div>

		<div>
			<label
				for="datePosted"
				class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
			>
				Date Posted
			</label>
			<select
				id="datePosted"
				bind:value={datePosted}
				disabled={saving}
				class="font-body w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 text-sm text-[var(--color-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50"
			>
				<option value="">Any</option>
				<option value="24h">Last 24 hours</option>
				<option value="week">Past week</option>
				<option value="month">Past month</option>
			</select>
		</div>
	</div>

	<div class="mt-4">
		<span class="font-mono mb-2 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
			Experience Level
		</span>
		<div class="flex flex-wrap gap-2">
			{#each experienceLevelOptions as opt}
				<label
					class="flex cursor-pointer items-center gap-1.5 border-2 px-2.5 py-1.5 font-mono text-xs transition-colors {experienceLevel.includes(opt.value) ? 'border-[var(--color-foreground)] bg-[var(--color-primary)] text-[var(--color-primary-foreground)]' : 'border-[var(--color-muted)] bg-white text-[var(--color-muted-foreground)] hover:border-[var(--color-foreground)]'}"
				>
					<input
						type="checkbox"
						checked={experienceLevel.includes(opt.value)}
						onchange={() => (experienceLevel = toggleItem(experienceLevel, opt.value))}
						disabled={saving}
						class="sr-only"
					/>
					{opt.label}
				</label>
			{/each}
		</div>
	</div>

	<div class="mt-4">
		<span class="font-mono mb-2 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
			Job Type
		</span>
		<div class="flex flex-wrap gap-2">
			{#each jobTypeOptions as opt}
				<label
					class="flex cursor-pointer items-center gap-1.5 border-2 px-2.5 py-1.5 font-mono text-xs transition-colors {jobType.includes(opt.value) ? 'border-[var(--color-foreground)] bg-[var(--color-primary)] text-[var(--color-primary-foreground)]' : 'border-[var(--color-muted)] bg-white text-[var(--color-muted-foreground)] hover:border-[var(--color-foreground)]'}"
				>
					<input
						type="checkbox"
						checked={jobType.includes(opt.value)}
						onchange={() => (jobType = toggleItem(jobType, opt.value))}
						disabled={saving}
						class="sr-only"
					/>
					{opt.label}
				</label>
			{/each}
		</div>
	</div>

	<div class="mt-4 flex items-center gap-6">
		<label class="flex cursor-pointer items-center gap-2">
			<div
				class="relative h-6 w-10 border-2 border-[var(--color-foreground)] transition-colors {easyApplyOnly ? 'bg-[var(--color-primary)]' : 'bg-white'}"
			>
				<div
					class="absolute top-0.5 h-4 w-4 border border-[var(--color-foreground)] bg-white transition-all {easyApplyOnly ? 'left-4' : 'left-0.5'}"
				></div>
			</div>
			<input
				type="checkbox"
				bind:checked={easyApplyOnly}
				disabled={saving}
				class="sr-only"
			/>
			<span class="font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
				Easy Apply Only
			</span>
		</label>

		<div class="flex items-center gap-2">
			<label
				for="maxJobs"
				class="font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
			>
				Max Jobs
			</label>
			<input
				id="maxJobs"
				type="number"
				bind:value={maxJobs}
				min="1"
				max="500"
				disabled={saving}
				class="font-body w-20 border-2 border-[var(--color-foreground)] bg-white px-2 py-1 text-sm text-[var(--color-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50"
			/>
		</div>
	</div>

	{#if error}
		<div class="mt-4 border-2 border-[var(--color-error)] bg-red-50 px-3 py-2 font-mono text-xs text-[var(--color-error)]">
			{error}
		</div>
	{/if}

	<div class="mt-4 flex items-center gap-3">
		<button
			onclick={handleSave}
			disabled={saving}
			class="border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-4 py-2 font-mono text-xs uppercase tracking-wider text-[var(--color-primary-foreground)] shadow-brutal transition-all duration-200 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
		>
			{saving ? 'Saving...' : 'Save Preferences'}
		</button>
		{#if saved}
			<span class="font-mono text-xs text-[var(--color-success)]">Saved</span>
		{/if}
	</div>
</section>
