<script lang="ts">
	interface Props {
		statuses: string[];
		sources: string[];
		createdFrom: string;
		createdTo: string;
		search: string;
		onChange: () => void;
	}

	let {
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
		'completed',
		'pending',
		'approved',
		'declined',
		'retrying',
		'applying',
		'applied',
		'failed',
		'filtered_out',
		'scrape_failed',
	];
	const SOURCE_OPTIONS = ['linkedin', 'url', 'manual'];

	let searchInput = $state(search);
	let searchTimer: ReturnType<typeof setTimeout> | null = null;

	// Keep the local input in sync when `search` is changed externally
	// (e.g. summary-card toggles or a "clear all" reset from the parent).
	$effect(() => {
		searchInput = search;
	});

	function toggle(list: string[], value: string): string[] {
		return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
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
		statuses = [];
		sources = [];
		createdFrom = '';
		createdTo = '';
		search = '';
		searchInput = '';
		onChange();
	}

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
			for="my-job-search"
			class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
		>
			Search (title, company, error)
		</label>
		<input
			id="my-job-search"
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
				for="my-created-from"
				class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
			>
				Created from
			</label>
			<input
				id="my-created-from"
				type="date"
				value={createdFrom}
				oninput={onCreatedFromChange}
				class="font-body w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
			/>
		</div>
		<div>
			<label
				for="my-created-to"
				class="font-mono mb-1 block text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]"
			>
				Created to
			</label>
			<input
				id="my-created-to"
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

	<div>
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
</div>
