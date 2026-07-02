<script lang="ts">
	import type { ApplyProfile, CustomAnswer, User } from '$lib/api/auth';
	import { updateProfile } from '$lib/api/settings';
	import { auth } from '$lib/stores/auth.svelte';

	let { user }: { user: User } = $props();

	// Local editable copies. `null` means "unknown" — the server-side classifier
	// aborts an application to `manual_required` rather than guessing a missing
	// value, so leaving a field blank is a deliberate (and safe) choice.
	const initial: ApplyProfile = user.apply_profile ?? {
		phone_country_code: null,
		years_experience: null,
		expected_salary: null,
		needs_visa_sponsorship: null,
		legally_authorized: null,
		willing_to_relocate: null,
		drivers_license: null,
	};

	let phoneCountryCode = $state(initial.phone_country_code ?? '');
	// `<input type="number">` binds to `number | null` (null when cleared), so we
	// keep this numeric rather than a string — never call string methods on it.
	let yearsExperience = $state<number | null>(initial.years_experience ?? null);
	let expectedSalary = $state(initial.expected_salary ?? '');
	let needsVisaSponsorship = $state(triToString(initial.needs_visa_sponsorship));
	let legallyAuthorized = $state(triToString(initial.legally_authorized));
	let willingToRelocate = $state(triToString(initial.willing_to_relocate));
	let driversLicense = $state(triToString(initial.drivers_license));
	let autoApply = $state(user.auto_apply ?? false);
	// Reusable answers captured from manual_required aborts. Editable here so the
	// user can review/remove stale entries; persisted on Save with the profile.
	let customAnswers = $state<CustomAnswer[]>(initial.custom_answers ?? []);

	function removeCustomAnswer(key: string) {
		customAnswers = customAnswers.filter((a) => a.key !== key);
	}

	let saving = $state(false);
	let saved = $state(false);
	let error = $state<string | null>(null);

	/** Map a nullable boolean to a tri-state <select> value. */
	function triToString(v: boolean | null | undefined): 'unset' | 'yes' | 'no' {
		if (v === true) return 'yes';
		if (v === false) return 'no';
		return 'unset';
	}

	/** Map a tri-state <select> value back to a nullable boolean. */
	function triToBool(v: string): boolean | null {
		if (v === 'yes') return true;
		if (v === 'no') return false;
		return null;
	}

	const triOptions: { value: string; label: string }[] = [
		{ value: 'unset', label: 'Not set' },
		{ value: 'yes', label: 'Yes' },
		{ value: 'no', label: 'No' },
	];

	function buildProfile(): ApplyProfile {
		return {
			phone_country_code: phoneCountryCode.trim() || null,
			years_experience: yearsExperience === null ? null : yearsExperience,
			expected_salary: expectedSalary.trim() || null,
			needs_visa_sponsorship: triToBool(needsVisaSponsorship),
			legally_authorized: triToBool(legallyAuthorized),
			willing_to_relocate: triToBool(willingToRelocate),
			drivers_license: triToBool(driversLicense),
			// Preserve captured answers — omitting them here would wipe the store
			// on the next save (the server replaces the whole apply_profile).
			custom_answers: customAnswers,
		};
	}

	async function handleSave() {
		saving = true;
		error = null;
		saved = false;

		if (yearsExperience !== null && (!Number.isFinite(yearsExperience) || yearsExperience < 0)) {
			error = 'Years of experience must be a non-negative number.';
			saving = false;
			return;
		}

		try {
			const updated = await updateProfile({
				apply_profile: buildProfile(),
				auto_apply: autoApply,
			});
			auth.setUser(updated);
			saved = true;
			setTimeout(() => (saved = false), 2000);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to update application profile';
		} finally {
			saving = false;
		}
	}

	const inputClass =
		'font-body w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 text-sm text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50';
	const labelClass =
		'font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]';
</script>

<section class="border-4 border-[var(--color-foreground)] bg-white p-6 shadow-brutal">
	<h2 class="font-heading mb-1 text-lg tracking-tight">Application Profile</h2>
	<p class="font-body mb-4 text-sm text-[var(--color-muted-foreground)]">
		Answers reused to fill LinkedIn Easy Apply screening questions. Leave a field blank if
		you'd rather decide per application — the agent never guesses, so an
		<span class="font-semibold">incomplete profile causes applies to abort to</span>
		<span class="font-mono text-[var(--color-foreground)]">manual required</span>.
	</p>

	<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
		<div>
			<label for="phone-country-code" class={labelClass}>Phone Country Code</label>
			<input
				id="phone-country-code"
				type="text"
				bind:value={phoneCountryCode}
				placeholder="e.g. +1"
				disabled={saving}
				class={inputClass}
			/>
		</div>

		<div>
			<label for="years-experience" class={labelClass}>Years of Experience</label>
			<input
				id="years-experience"
				type="number"
				min="0"
				bind:value={yearsExperience}
				placeholder="e.g. 8"
				disabled={saving}
				class={inputClass}
			/>
		</div>

		<div class="sm:col-span-2">
			<label for="expected-salary" class={labelClass}>Expected Salary</label>
			<input
				id="expected-salary"
				type="text"
				bind:value={expectedSalary}
				placeholder="e.g. 120000 USD"
				disabled={saving}
				class={inputClass}
			/>
		</div>

		<div>
			<label for="needs-visa" class={labelClass}>Needs Visa Sponsorship</label>
			<select id="needs-visa" bind:value={needsVisaSponsorship} disabled={saving} class={inputClass}>
				{#each triOptions as opt}
					<option value={opt.value}>{opt.label}</option>
				{/each}
			</select>
		</div>

		<div>
			<label for="legally-authorized" class={labelClass}>Legally Authorized to Work</label>
			<select
				id="legally-authorized"
				bind:value={legallyAuthorized}
				disabled={saving}
				class={inputClass}
			>
				{#each triOptions as opt}
					<option value={opt.value}>{opt.label}</option>
				{/each}
			</select>
		</div>

		<div>
			<label for="willing-to-relocate" class={labelClass}>Willing to Relocate</label>
			<select
				id="willing-to-relocate"
				bind:value={willingToRelocate}
				disabled={saving}
				class={inputClass}
			>
				{#each triOptions as opt}
					<option value={opt.value}>{opt.label}</option>
				{/each}
			</select>
		</div>

		<div>
			<label for="drivers-license" class={labelClass}>Driver's License</label>
			<select id="drivers-license" bind:value={driversLicense} disabled={saving} class={inputClass}>
				{#each triOptions as opt}
					<option value={opt.value}>{opt.label}</option>
				{/each}
			</select>
		</div>
	</div>

	{#if customAnswers.length > 0}
		<div class="mt-5 border-t-2 border-dashed border-[var(--color-muted)] pt-4">
			<h3 class="font-heading mb-1 text-sm tracking-tight">Saved answers</h3>
			<p class="font-body mb-3 text-xs text-[var(--color-muted-foreground)]">
				Captured from questions you answered when an application needed manual input. These
				auto-fill matching questions on future applications. Remove any that are stale, then Save.
			</p>
			<ul class="flex flex-col gap-2">
				{#each customAnswers as ans (ans.key)}
					<li
						class="flex items-start justify-between gap-3 border-2 border-[var(--color-foreground)] bg-[var(--color-muted)]/30 px-3 py-2"
					>
						<div class="min-w-0">
							<div class="font-body truncate text-sm font-semibold" title={ans.label}>
								{ans.label}
							</div>
							<div class="font-mono truncate text-xs text-[var(--color-muted-foreground)]">
								{ans.value}
							</div>
						</div>
						<button
							type="button"
							onclick={() => removeCustomAnswer(ans.key)}
							disabled={saving}
							class="font-mono shrink-0 border-2 border-[var(--color-foreground)] bg-red-100 px-2 py-1 text-[10px] uppercase tracking-wider text-red-900 hover:bg-red-200 disabled:opacity-50"
						>
							Remove
						</button>
					</li>
				{/each}
			</ul>
		</div>
	{/if}

	<div class="mt-5 border-t-2 border-dashed border-[var(--color-muted)] pt-4">
		<label class="flex items-start gap-3">
			<input
				type="checkbox"
				bind:checked={autoApply}
				disabled={saving}
				class="mt-0.5 h-5 w-5 border-2 border-[var(--color-foreground)] accent-[var(--color-primary)]"
			/>
			<span>
				<span class="font-body block text-sm font-semibold text-[var(--color-foreground)]">
					Auto-apply to matching jobs
				</span>
				<span class="font-body block text-xs text-[var(--color-muted-foreground)]">
					When enabled, jobs that pass your filter apply automatically without HITL review.
					Requires the browser extension to be connected.
				</span>
			</span>
		</label>
	</div>

	{#if error}
		<div
			class="mt-4 border-2 border-[var(--color-error)] bg-red-50 px-3 py-2 font-mono text-xs text-[var(--color-error)]"
		>
			{error}
		</div>
	{/if}

	<div class="mt-5 flex items-center gap-3">
		<button
			onclick={handleSave}
			disabled={saving}
			class="border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-4 py-2 font-mono text-xs uppercase tracking-wider text-[var(--color-primary-foreground)] shadow-brutal transition-all duration-200 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
		>
			{saving ? 'Saving...' : 'Save'}
		</button>
		{#if saved}
			<span class="font-mono text-xs text-[var(--color-success)]">Saved</span>
		{/if}
	</div>
</section>
