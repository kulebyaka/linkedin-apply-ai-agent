<script lang="ts">
	import type { AdminJobRecord, PendingQuestion } from '$lib/api/admin';
	import type { QuestionAnswer } from '$lib/api/jobs';

	interface Props {
		job: AdminJobRecord;
		submitting?: boolean;
		onCancel: () => void;
		onSubmit: (answers: QuestionAnswer[]) => void;
	}

	let { job, submitting = false, onCancel, onSubmit }: Props = $props();

	const questions = $derived<PendingQuestion[]>(job.pending_questions ?? []);

	// One editable value per question, keyed by selector. Seeded once at mount
	// from the job (the modal is recreated per open, so a fresh {} is correct).
	// Do NOT seed inside an $effect that also reads `values` — reassigning a
	// $state the effect reads re-invalidates it and loops (effect_update_depth).
	let values = $state<Record<string, string>>(
		Object.fromEntries((job.pending_questions ?? []).map((q) => [q.selector, '']))
	);

	function isChoice(q: PendingQuestion): boolean {
		return (
			(q.field_type === 'radio' || q.field_type === 'select' || q.field_type === 'listbox') &&
			q.options.length > 0
		);
	}

	const allAnswered = $derived(questions.every((q) => (values[q.selector] ?? '').trim() !== ''));

	function submit() {
		if (submitting || !allAnswered) return;
		const answers: QuestionAnswer[] = questions.map((q) => ({
			label: q.label,
			field_type: q.field_type,
			value: values[q.selector] ?? '',
			options: q.options,
			kind: q.kind ?? null
		}));
		onSubmit(answers);
	}

	const jobTitle = $derived((job.job_posting?.title as string | undefined) ?? job.job_id);
	const jobCompany = $derived(job.job_posting?.company as string | undefined);
</script>

<div
	class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
	role="dialog"
	aria-modal="true"
	aria-labelledby="answer-title"
>
	<div
		class="flex max-h-[85vh] w-full max-w-lg flex-col border-4 border-[var(--color-foreground)] bg-white shadow-brutal"
	>
		<div class="border-b-2 border-[var(--color-foreground)] bg-[var(--color-muted)] px-4 py-3">
			<h2 id="answer-title" class="font-heading text-lg tracking-tight">Answer screening questions</h2>
			<p class="font-mono mt-1 text-[10px] uppercase tracking-wider text-[var(--color-muted-foreground)]">
				{jobTitle}{#if jobCompany} · {jobCompany}{/if}
			</p>
		</div>

		<div class="flex flex-col gap-4 overflow-y-auto px-4 py-4">
			<p class="text-sm text-[var(--color-muted-foreground)]">
				We couldn't auto-fill these fields. Your answers are saved to your Application Profile and
				reused on future applications with the same question.
			</p>

			{#each questions as q (q.selector)}
				<div class="flex flex-col gap-1">
					<label for={`q-${q.selector}`} class="font-body text-sm font-bold">
						{q.label || '(unlabeled field)'}
						{#if q.required}<span class="text-red-700">*</span>{/if}
					</label>

					{#if isChoice(q)}
						<select
							id={`q-${q.selector}`}
							bind:value={values[q.selector]}
							disabled={submitting}
							class="font-body w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50"
						>
							<option value="" disabled>Select an option…</option>
							{#each q.options as opt}
								<option value={opt}>{opt}</option>
							{/each}
						</select>
					{:else if q.field_type === 'checkbox'}
						<label class="flex items-center gap-2 text-sm">
							<input
								type="checkbox"
								checked={values[q.selector] === 'true'}
								disabled={submitting}
								onchange={(e) =>
									(values[q.selector] = (e.currentTarget as HTMLInputElement).checked
										? 'true'
										: 'false')}
							/>
							Yes
						</label>
					{:else}
						<input
							id={`q-${q.selector}`}
							type={q.field_type === 'number'
								? 'number'
								: q.field_type === 'email'
									? 'email'
									: q.field_type === 'tel'
										? 'tel'
										: 'text'}
							bind:value={values[q.selector]}
							disabled={submitting}
							class="font-body w-full border-2 border-[var(--color-foreground)] bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:opacity-50"
						/>
					{/if}
				</div>
			{/each}
		</div>

		<div class="flex justify-end gap-2 border-t-2 border-[var(--color-foreground)] px-4 py-3">
			<button
				type="button"
				onclick={onCancel}
				disabled={submitting}
				class="font-mono border-2 border-[var(--color-foreground)] bg-white px-3 py-1.5 text-xs uppercase tracking-wider hover:bg-[var(--color-muted)] disabled:opacity-50"
			>
				Cancel
			</button>
			<button
				type="button"
				onclick={submit}
				disabled={submitting || !allAnswered}
				class="font-mono border-2 border-[var(--color-foreground)] bg-amber-200 px-3 py-1.5 text-xs uppercase tracking-wider text-amber-900 shadow-brutal hover:-translate-y-0.5 disabled:opacity-50 disabled:hover:translate-y-0"
			>
				{submitting ? 'Saving…' : 'Save & Apply now'}
			</button>
		</div>
	</div>
</div>
