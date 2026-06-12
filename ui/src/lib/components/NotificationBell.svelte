<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import { notifications } from '$lib/stores/notifications.svelte';
	import type { Notification } from '$lib/api/notifications';
	import { relativeTime } from '$lib/utils/time';

	let open = $state(false);

	onMount(() => {
		notifications.start();
	});

	onDestroy(() => {
		notifications.stop();
	});

	async function toggle() {
		open = !open;
		if (open) {
			await notifications.refreshList();
		}
	}

	function close() {
		open = false;
	}

	async function handleClick(n: Notification) {
		await notifications.markRead(n.id);
		if (n.action_url) {
			close();
			goto(n.action_url);
		}
	}
</script>

<div class="relative">
	<button
		onclick={toggle}
		class="relative flex h-9 w-9 items-center justify-center border-2 border-[var(--color-foreground)] bg-[var(--color-background)] shadow-brutal transition-all hover:-translate-y-0.5"
		aria-label="Notifications"
		aria-expanded={open}
	>
		<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
			<path
				stroke-linecap="round"
				stroke-linejoin="round"
				stroke-width="2"
				d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"
			/>
		</svg>
		{#if notifications.unread > 0}
			<span
				class="absolute -right-2 -top-2 flex h-5 min-w-[1.25rem] items-center justify-center border-2 border-[var(--color-foreground)] bg-[var(--color-primary)] px-1 font-mono text-[10px] font-bold leading-none"
			>
				{notifications.unread > 9 ? '9+' : notifications.unread}
			</span>
		{/if}
	</button>

	{#if open}
		<!-- Backdrop to close on outside click -->
		<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
		<div class="fixed inset-0 z-40" onclick={close}></div>

		<div
			class="absolute right-0 z-50 mt-2 w-80 border-4 border-[var(--color-foreground)] bg-[var(--color-background)] shadow-brutal-xl sm:w-96"
		>
			<div
				class="flex items-center justify-between border-b-2 border-[var(--color-foreground)] bg-[var(--color-muted)] px-3 py-2"
			>
				<span class="font-mono text-xs uppercase tracking-wider">Notifications</span>
				{#if notifications.unread > 0}
					<button
						onclick={() => notifications.markAllRead()}
						class="font-mono text-[10px] uppercase tracking-wider text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] hover:underline"
					>
						Mark all read
					</button>
				{/if}
			</div>

			<div class="max-h-96 overflow-y-auto">
				{#if notifications.loading && notifications.items.length === 0}
					<p class="px-3 py-6 text-center font-mono text-xs text-[var(--color-muted-foreground)]">
						Loading…
					</p>
				{:else if notifications.items.length === 0}
					<p class="px-3 py-6 text-center font-mono text-xs text-[var(--color-muted-foreground)]">
						No notifications
					</p>
				{:else}
					{#each notifications.items as n (n.id)}
						<button
							onclick={() => handleClick(n)}
							class="block w-full border-b border-[var(--color-muted)] px-3 py-3 text-left transition-colors hover:bg-[var(--color-muted)]/40 {n.read
								? 'opacity-60'
								: ''}"
						>
							<div class="flex items-start gap-2">
								{#if !n.read}
									<span class="mt-1.5 h-2 w-2 flex-shrink-0 bg-[var(--color-primary)]"></span>
								{:else}
									<span class="mt-1.5 h-2 w-2 flex-shrink-0"></span>
								{/if}
								<div class="min-w-0 flex-1">
									<p class="font-mono text-xs font-bold">{n.title}</p>
									{#if n.body}
										<p class="mt-0.5 font-body text-xs text-[var(--color-muted-foreground)]">
											{n.body}
										</p>
									{/if}
									<p class="mt-1 font-mono text-[10px] uppercase tracking-wider text-[var(--color-muted-foreground)]">
										{relativeTime(n.created_at)}{#if n.action_url} · view{/if}
									</p>
								</div>
							</div>
						</button>
					{/each}
				{/if}
			</div>
		</div>
	{/if}
</div>
