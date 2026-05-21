<script lang="ts">
	import { onMount } from 'svelte';
	import { listUsers, type AdminUserRow } from '$lib/api/admin';

	interface Props {
		userIds: string[];
		statuses: string[];
		sources: string[];
		createdFrom: string;
		createdTo: string;
		search: string;
		onChange: () => void;
	}

	let {
		userIds = $bindable(),
		statuses = $bindable(),
		sources = $bindable(),
		createdFrom = $bindable(),
		createdTo = $bindable(),
		search = $bindable(),
		onChange,
	}: Props = $props();

	const STATUS_OPTIONS = [
		'queued',
		'processing',
		'cv_ready',
		'pending_review',
		'approved',
		'declined',
		'retrying',
		'applying',
		'applied',
		'failed',
		'filtered_out',
		'completed',
		'pending',
	];
	const SOURCE_OPTIONS = ['linkedin', 'url', 'manual'];

	let users = $state<AdminUserRow[]>([]);
	let usersLoading = $state(false);
	let searchInput = $state(search);
	let searchTimer: ReturnType<typeof setTimeout> | null = null;

	onMount(async () => {
		usersLoading = true;
		try {
			const resp = await listUsers(200, 0);
			users = resp.items;
		} catch (err) {
			console.error('Failed to load users for filter', err);
		} finally {
			usersLoading = false;
		}
	});

	function toggle(list: string[], value: string): string[] {
		return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
	}

	function onToggleUser(id: string) {
		userIds = toggle(userIds, id);
		onChange();
	}

	function onToggleStatus(s: string) {
		statuses = toggle(statuses, s);
		onChange();
	}

	function onToggleSource(s: string) {
		sources = toggle(sources, s);
		onChange();
	}

	function onCreatedFromChange(e: Event) {
		createdFrom = (e.target as HTMLInputElement).value;
		onChange();
	}

	function onCreatedToChange(e: Event) {
		createdTo = (e.target as HTMLInputElement).value;
		onChange();
	}

	function onSearchInput(e: Event) {
		searchInput = (e.target as HTMLInputElement).value;
		if (searchTimer) clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			search = searchInput;
			onChange();
		}, 300);
	}

	function clearAll() {
		userIds = [];
		statuses = [];
		sources = [];
		createdFrom = '';
		createdTo = '';
		search = '';
		searchInput = '';
		onChange();
	}

	const userById = $derived(new Map(users.map((u) => [u.user.id, u.user.email])));

	const chipBase =
		'inline-flex items-center gap-1 border-2 border-[var(--color-foreground)] px-2 py-0.5 font-mono text-xs uppercase tracking-wider transition-colors';
</script>

<div class="border-4 border-[var(--color-foreground)] bg-white p-4 shadow-brutal">
	<div class="mb-3 flex items-center justify-between">
		<h3 class="font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
			Filters
		</h3>
		<button
			type="button"
			onclick={clearAll}
			class="font-mono text-xs uppercase tracking-wider text-[var(--color-muted-foreground)] underline hover:text-[var(--color-foreground)]"
		>
			Clear all
		</button>
	</div>

	<div class="mb-3">
		<label
			for="admin-job-search"
			class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
		>
			Search (title, company, error)
		</label>
		<input
			id="admin-job-search"
			type="text"
			value={searchInput}
			oninput={onSearchInput}
			placeholder="Type to search…"
			class="font-body w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 text-sm text-[var(--color-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
		/>
	</div>

	<div class="mb-3 grid gap-3 sm:grid-cols-2">
		<div>
			<label
				for="admin-created-from"
				class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
			>
				Created from
			</label>
			<input
				id="admin-created-from"
				type="date"
				value={createdFrom}
				oninput={onCreatedFromChange}
				class="font-body w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
			/>
		</div>
		<div>
			<label
				for="admin-created-to"
				class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
			>
				Created to
			</label>
			<input
				id="admin-created-to"
				type="date"
				value={createdTo}
				oninput={onCreatedToChange}
				class="font-body w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
			/>
		</div>
	</div>

	<div class="mb-3">
		<div class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
			Status
		</div>
		<div class="flex flex-wrap gap-1.5">
			{#each STATUS_OPTIONS as s}
				{@const active = statuses.includes(s)}
				<button
					type="button"
					onclick={() => onToggleStatus(s)}
					class="{chipBase} {active
						? 'bg-[var(--color-foreground)] text-white'
						: 'bg-white text-[var(--color-foreground)] hover:bg-[var(--color-muted)]'}"
				>
					{s}
				</button>
			{/each}
		</div>
	</div>

	<div class="mb-3">
		<div class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
			Source
		</div>
		<div class="flex flex-wrap gap-1.5">
			{#each SOURCE_OPTIONS as s}
				{@const active = sources.includes(s)}
				<button
					type="button"
					onclick={() => onToggleSource(s)}
					class="{chipBase} {active
						? 'bg-[var(--color-foreground)] text-white'
						: 'bg-white text-[var(--color-foreground)] hover:bg-[var(--color-muted)]'}"
				>
					{s}
				</button>
			{/each}
		</div>
	</div>

	<div>
		<div class="font-mono mb-1 flex items-center gap-2 text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
			<span>Users</span>
			{#if usersLoading}
				<span class="normal-case">loading…</span>
			{/if}
		</div>
		{#if userIds.length > 0}
			<div class="mb-2 flex flex-wrap gap-1.5">
				{#each userIds as id}
					<button
						type="button"
						onclick={() => onToggleUser(id)}
						class="{chipBase} bg-[var(--color-foreground)] text-white"
						title="Click to remove"
					>
						<span class="normal-case">{userById.get(id) ?? id}</span>
						<span aria-hidden="true">×</span>
					</button>
				{/each}
			</div>
		{/if}
		{#if users.length > 0}
			<select
				onchange={(e) => {
					const v = (e.target as HTMLSelectElement).value;
					if (v) {
						onToggleUser(v);
						(e.target as HTMLSelectElement).value = '';
					}
				}}
				class="font-body w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
			>
				<option value="">Add user filter…</option>
				{#each users as row}
					{#if !userIds.includes(row.user.id)}
						<option value={row.user.id}>{row.user.email}</option>
					{/if}
				{/each}
			</select>
		{/if}
	</div>
</div>
