<script lang="ts">
	import { onMount } from 'svelte';

	interface Props {
		message: string;
		type: 'error' | 'success' | 'info';
		duration?: number;
		onClose: () => void;
	}

	let { message, type, duration = 5000, onClose }: Props = $props();

	onMount(() => {
		if (duration > 0) {
			const timer = setTimeout(() => {
				onClose();
			}, duration);

			return () => clearTimeout(timer);
		}
	});

	// Compute icon and colors based on type
	const config = $derived({
		error: {
			bgColor: 'hsl(0 100% 97%)',
			borderColor: 'var(--color-destructive)',
			textColor: 'var(--color-foreground)',
			iconColor: 'var(--color-destructive)',
			icon: 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z'
		},
		success: {
			bgColor: 'hsl(140 100% 97%)',
			borderColor: 'var(--color-success)',
			textColor: 'var(--color-foreground)',
			iconColor: 'var(--color-success)',
			icon: 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z'
		},
		info: {
			bgColor: 'var(--color-background)',
			borderColor: 'var(--color-primary)',
			textColor: 'var(--color-foreground)',
			iconColor: 'var(--color-primary)',
			icon: 'M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z'
		}
	}[type]);
</script>

<div
	class="fixed top-6 right-6 max-w-md w-full z-50 animate-slide-in-right"
	role="alert"
	aria-live="assertive"
>
	<div
		class="flex items-start p-5 border-2 shadow-brutal transition-all duration-200"
		style="background-color: {config.bgColor}; border-color: {config.borderColor};"
	>
		<!-- Icon -->
		<div class="flex-shrink-0">
			<svg
				class="h-6 w-6"
				style="color: {config.iconColor};"
				fill="none"
				stroke="currentColor"
				viewBox="0 0 24 24"
			>
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d={config.icon}
				></path>
			</svg>
		</div>

		<!-- Message -->
		<div class="ml-4 flex-1">
			<p class="text-sm font-medium leading-relaxed" style="color: {config.textColor};">{message}</p>
		</div>

		<!-- Close button -->
		<div class="ml-4 flex-shrink-0 flex">
			<button
				onclick={onClose}
				class="inline-flex hover:opacity-70 focus:outline-none transition-opacity duration-150"
				style="color: {config.textColor};"
			>
				<span class="sr-only">Close</span>
				<svg class="h-5 w-5" fill="currentColor" viewBox="0 0 20 20">
					<path
						fill-rule="evenodd"
						d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
						clip-rule="evenodd"
					></path>
				</svg>
			</button>
		</div>
	</div>
</div>

<style>
	@keyframes slide-in-right {
		from {
			transform: translateX(100%);
			opacity: 0;
		}
		to {
			transform: translateX(0);
			opacity: 1;
		}
	}

	.animate-slide-in-right {
		animation: slide-in-right 0.3s ease-out;
	}
</style>
