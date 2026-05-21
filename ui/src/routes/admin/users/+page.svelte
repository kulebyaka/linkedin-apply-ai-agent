<script lang="ts">
	import { onMount } from 'svelte';
	import {
		listUsers,
		setUserRole,
		type AdminUserRow,
	} from '$lib/api/admin';
	import type { UserRole } from '$lib/api/auth';
	import { auth } from '$lib/stores/auth.svelte';
	import ToastNotification from '$lib/components/ToastNotification.svelte';

	const ROLES: UserRole[] = ['trial', 'premium', 'admin'];

	let rows = $state<AdminUserRow[]>([]);
	let loading = $state(false);
	let initialLoadDone = $state(false);
	let savingUserId = $state<string | null>(null);
	let confirmChange = $state<{ userId: string; email: string; from: UserRole; to: UserRole } | null>(null);

	let toast = $state<{ message: string; type: 'success' | 'error' | 'info' } | null>(null);

	function showToast(message: string, type: 'success' | 'error' | 'info' = 'info') {
		toast = { message, type };
	}

	function clearToast() {
		toast = null;
	}

	async function fetchUsers() {
		loading = true;
		try {
			const resp = await listUsers(200, 0);
			rows = resp.items;
			initialLoadDone = true;
		} catch (err) {
			showToast(err instanceof Error ? err.message : 'Failed to load users', 'error');
		} finally {
			loading = false;
		}
	}

	function onRoleSelect(row: AdminUserRow, target: UserRole) {
		const current = row.user.role;
		if (target === current) return;

		const isSelf = auth.user?.id === row.user.id;
		if (isSelf && target !== 'admin') {
			showToast('You cannot change your own role away from admin.', 'error');
			return;
		}

		confirmChange = {
			userId: row.user.id,
			email: row.user.email,
			from: current,
			to: target,
		};
	}

	async function performRoleChange() {
		if (!confirmChange) return;
		const { userId, to } = confirmChange;
		const previous = rows.find((r) => r.user.id === userId)?.user.role ?? null;
		confirmChange = null;
		savingUserId = userId;

		// Optimistic update
		rows = rows.map((r) =>
			r.user.id === userId ? { ...r, user: { ...r.user, role: to } } : r,
		);

		try {
			await setUserRole(userId, to);
			showToast(`Role updated to ${to}`, 'success');
		} catch (err) {
			// Rollback on error
			if (previous) {
				rows = rows.map((r) =>
					r.user.id === userId ? { ...r, user: { ...r.user, role: previous } } : r,
				);
			}
			showToast(err instanceof Error ? err.message : 'Failed to update role', 'error');
		} finally {
			savingUserId = null;
		}
	}

	function cancelRoleChange() {
		confirmChange = null;
	}

	function formatDate(iso: string | null | undefined): string {
		if (!iso) return '—';
		try {
			return new Date(iso).toLocaleString();
		} catch {
			return iso;
		}
	}

	function formatRelative(iso: string | null | undefined): string {
		if (!iso) return '—';
		const t = new Date(iso).getTime();
		if (Number.isNaN(t)) return iso;
		const diff = Date.now() - t;
		const abs = Math.abs(diff);
		const sec = Math.round(abs / 1000);
		if (sec < 60) return diff >= 0 ? `${sec}s ago` : `in ${sec}s`;
		const min = Math.round(sec / 60);
		if (min < 60) return diff >= 0 ? `${min}m ago` : `in ${min}m`;
		const hr = Math.round(min / 60);
		if (hr < 48) return diff >= 0 ? `${hr}h ago` : `in ${hr}h`;
		const day = Math.round(hr / 24);
		return diff >= 0 ? `${day}d ago` : `in ${day}d`;
	}

	function roleBadge(r: UserRole): string {
		const base =
			'inline-block border-2 border-[var(--color-foreground)] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider';
		if (r === 'admin') return `${base} bg-red-200 text-red-900`;
		if (r === 'premium') return `${base} bg-emerald-200 text-emerald-900`;
		return `${base} bg-white text-[var(--color-foreground)]`;
	}

	function countOf(row: AdminUserRow, status: string): number {
		return row.job_counts?.[status] ?? 0;
	}

	function totalJobs(row: AdminUserRow): number {
		const counts = row.job_counts ?? {};
		let n = 0;
		for (const v of Object.values(counts)) n += v;
		return n;
	}

	onMount(() => {
		fetchUsers();
	});

	const selfId = $derived(auth.user?.id ?? null);
</script>

<svelte:head>
	<title>Admin · Users</title>
</svelte:head>

<div class="flex flex-col gap-4">
	<header class="flex items-center justify-between">
		<h1 class="font-heading text-2xl tracking-tight">Users</h1>
		<button
			type="button"
			onclick={fetchUsers}
			class="font-mono border-2 border-[var(--color-foreground)] bg-white px-3 py-1.5 text-xs uppercase tracking-wider shadow-brutal hover:-translate-y-0.5"
		>
			Refresh
		</button>
	</header>

	<div class="overflow-x-auto border-4 border-[var(--color-foreground)] bg-white shadow-brutal">
		<table class="w-full border-collapse text-sm">
			<thead class="border-b-2 border-[var(--color-foreground)] bg-[var(--color-muted)]">
				<tr>
					<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">Email</th>
					<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">Role</th>
					<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">Created</th>
					<th class="font-mono px-3 py-2 text-right text-xs uppercase tracking-wider">Queued</th>
					<th class="font-mono px-3 py-2 text-right text-xs uppercase tracking-wider">Processing</th>
					<th class="font-mono px-3 py-2 text-right text-xs uppercase tracking-wider">Completed</th>
					<th class="font-mono px-3 py-2 text-right text-xs uppercase tracking-wider">Failed</th>
					<th class="font-mono px-3 py-2 text-right text-xs uppercase tracking-wider">Total</th>
					<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">Last job</th>
					<th class="font-mono px-3 py-2 text-left text-xs uppercase tracking-wider">Change role</th>
				</tr>
			</thead>
			<tbody>
				{#if loading && !initialLoadDone}
					<tr>
						<td colspan="10" class="px-3 py-8 text-center font-mono text-xs text-[var(--color-muted-foreground)]">
							Loading…
						</td>
					</tr>
				{:else if rows.length === 0}
					<tr>
						<td colspan="10" class="px-3 py-8 text-center font-mono text-xs text-[var(--color-muted-foreground)]">
							No users found.
						</td>
					</tr>
				{:else}
					{#each rows as row (row.user.id)}
						{@const isSelf = selfId === row.user.id}
						{@const saving = savingUserId === row.user.id}
						<tr class="border-b border-[var(--color-muted)] hover:bg-[var(--color-muted)]/40">
							<td class="px-3 py-2 font-mono text-xs">
								{row.user.email}
								{#if isSelf}
									<span class="ml-1 font-mono text-[10px] uppercase text-[var(--color-muted-foreground)]">(you)</span>
								{/if}
							</td>
							<td class="px-3 py-2">
								<span class={roleBadge(row.user.role)}>{row.user.role}</span>
							</td>
							<td class="px-3 py-2 font-mono text-xs" title={formatDate(row.user.created_at)}>
								{formatRelative(row.user.created_at)}
							</td>
							<td class="px-3 py-2 text-right font-mono text-xs">{countOf(row, 'queued')}</td>
							<td class="px-3 py-2 text-right font-mono text-xs">{countOf(row, 'processing')}</td>
							<td class="px-3 py-2 text-right font-mono text-xs">{countOf(row, 'completed')}</td>
							<td class="px-3 py-2 text-right font-mono text-xs">{countOf(row, 'failed')}</td>
							<td class="px-3 py-2 text-right font-mono text-xs">{totalJobs(row)}</td>
							<td class="px-3 py-2 font-mono text-xs" title={formatDate(row.last_job_at)}>
								{formatRelative(row.last_job_at)}
							</td>
							<td class="px-3 py-2">
								<select
									value={row.user.role}
									disabled={saving}
									onchange={(e) =>
										onRoleSelect(row, (e.currentTarget as HTMLSelectElement).value as UserRole)}
									aria-label={`Change role for ${row.user.email}`}
									class="font-mono border-2 border-[var(--color-foreground)] bg-white px-2 py-1 text-xs uppercase tracking-wider disabled:opacity-50"
								>
									{#each ROLES as r}
										<option value={r} disabled={isSelf && r !== 'admin' && row.user.role === 'admin'}>
											{r}
										</option>
									{/each}
								</select>
								{#if saving}
									<span class="ml-2 font-mono text-[10px] uppercase text-[var(--color-muted-foreground)]">
										Saving…
									</span>
								{/if}
							</td>
						</tr>
					{/each}
				{/if}
			</tbody>
		</table>
	</div>
</div>

{#if confirmChange}
	<div
		class="fixed inset-0 z-40 flex items-center justify-center bg-black/40"
		role="dialog"
		aria-modal="true"
	>
		<div class="w-full max-w-md border-4 border-[var(--color-foreground)] bg-white p-6 shadow-brutal">
			<h2 class="font-heading mb-2 text-lg tracking-tight">Confirm role change</h2>
			<p class="font-mono mb-4 text-xs text-[var(--color-muted-foreground)]">
				Change role of <span class="font-bold">{confirmChange.email}</span> from
				<span class={roleBadge(confirmChange.from)}>{confirmChange.from}</span>
				to <span class={roleBadge(confirmChange.to)}>{confirmChange.to}</span>?
			</p>
			<div class="flex justify-end gap-2">
				<button
					type="button"
					onclick={cancelRoleChange}
					class="font-mono border-2 border-[var(--color-foreground)] bg-white px-3 py-1.5 text-xs uppercase tracking-wider hover:bg-[var(--color-muted)]"
				>
					Cancel
				</button>
				<button
					type="button"
					onclick={performRoleChange}
					class="font-mono border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-3 py-1.5 text-xs uppercase tracking-wider text-[var(--color-primary-foreground)] hover:-translate-y-0.5"
				>
					Confirm
				</button>
			</div>
		</div>
	</div>
{/if}

{#if toast}
	<ToastNotification message={toast.message} type={toast.type} onClose={clearToast} />
{/if}
