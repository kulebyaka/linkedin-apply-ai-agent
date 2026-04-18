<script lang="ts">
	import { onMount } from 'svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { getSearchPreferences, getFilterPreferences } from '$lib/api/settings';
	import type { UserSearchPreferences } from '$lib/api/auth';
	import type { UserFilterPreferences } from '$lib/types/index';
	import ProfileSection from '$lib/components/settings/ProfileSection.svelte';
	import CVUploadSection from '$lib/components/settings/CVUploadSection.svelte';
	import SearchPreferencesSection from '$lib/components/settings/SearchPreferencesSection.svelte';
	import FilterPreferencesSection from '$lib/components/settings/FilterPreferencesSection.svelte';
	import ModelPreferencesSection from '$lib/components/settings/ModelPreferencesSection.svelte';
	import StartSearchSection from '$lib/components/settings/StartSearchSection.svelte';

	let searchPrefs = $state<UserSearchPreferences | null>(null);
	let filterPrefs = $state<UserFilterPreferences | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	onMount(async () => {
		try {
			[searchPrefs, filterPrefs] = await Promise.all([
				getSearchPreferences(),
				getFilterPreferences(),
			]);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to load settings';
		} finally {
			loading = false;
		}
	});
</script>

<svelte:head>
	<title>Settings - Job Application Agent</title>
</svelte:head>

<div class="grain-texture min-h-screen bg-[var(--color-background)] px-4 py-8 sm:px-6 lg:px-8">
	<div class="mx-auto max-w-2xl">
		<h1 class="font-heading mb-8 text-3xl tracking-tight text-[var(--color-foreground)]">
			Settings
		</h1>

		{#if loading}
			<div class="flex items-center justify-center py-16">
				<p class="font-mono text-sm text-[var(--color-muted-foreground)]">Loading settings...</p>
			</div>
		{:else if error}
			<div class="border-4 border-[var(--color-error)] bg-red-50 p-6">
				<p class="font-mono text-sm text-[var(--color-error)]">{error}</p>
			</div>
		{:else if auth.user}
			<div class="flex flex-col gap-6">
				<ProfileSection user={auth.user} />
				<CVUploadSection user={auth.user} />
				{#if searchPrefs}
					<SearchPreferencesSection prefs={searchPrefs} />
				{/if}
				{#if filterPrefs}
					<FilterPreferencesSection prefs={filterPrefs} />
				{/if}
				<ModelPreferencesSection />
				<StartSearchSection />
			</div>
		{/if}
	</div>
</div>
