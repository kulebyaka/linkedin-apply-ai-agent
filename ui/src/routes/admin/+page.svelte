<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';

	onMount(() => {
		goto('/admin/jobs', { replaceState: true });
	});
</script>

<div class="flex min-h-[40vh] items-center justify-center">
	<p class="font-mono text-sm text-[var(--color-muted-foreground)]">Redirecting to admin jobs...</p>
</div>
