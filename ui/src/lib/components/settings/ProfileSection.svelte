<script lang="ts">
	import type { User } from '$lib/api/auth';
	import { updateProfile } from '$lib/api/settings';
	import { auth } from '$lib/stores/auth.svelte';

	let { user }: { user: User } = $props();

	let displayName = $state(user.display_name);
	let saving = $state(false);
	let saved = $state(false);
	let error = $state<string | null>(null);

	async function handleSave() {
		saving = true;
		error = null;
		saved = false;

		try {
			const updated = await updateProfile({ display_name: displayName });
			auth.setUser(updated);
			saved = true;
			setTimeout(() => (saved = false), 2000);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to update profile';
		} finally {
			saving = false;
		}
	}
</script>

<section class="border-4 border-[var(--color-foreground)] bg-white p-6 shadow-brutal">
	<h2 class="font-heading mb-4 text-lg tracking-tight">Profile</h2>

	<div class="mb-4">
		<label
			for="email"
			class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
		>
			Email
		</label>
		<input
			id="email"
			type="email"
			value={user.email}
			disabled
			class="font-body w-full border-2 border-[var(--color-muted)] bg-[var(--color-background)] px-3 py-2 text-sm text-[var(--color-muted-foreground)] opacity-70"
		/>
	</div>

	<div class="mb-4">
		<label
			for="displayName"
			class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
		>
			Display Name
		</label>
		<input
			id="displayName"
			type="text"
			bind:value={displayName}
			placeholder="Your name"
			disabled={saving}
			class="font-body w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 text-sm text-[var(--color-foreground)] placeholder:text-[var(--color-muted-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50"
		/>
	</div>

	{#if error}
		<div class="mb-4 border-2 border-[var(--color-error)] bg-red-50 px-3 py-2 font-mono text-xs text-[var(--color-error)]">
			{error}
		</div>
	{/if}

	<div class="flex items-center gap-3">
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
