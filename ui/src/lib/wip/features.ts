export const WIP = {
	AUTO_APPLY: {
		label: 'Auto-Apply',
		tooltip:
			"Auto-apply is coming soon. Approve marks the job as reviewed — open it in LinkedIn to apply manually.",
	},
	HISTORY_VIEW: {
		label: 'History view',
		tooltip: 'The API records every decision; the UI surface is next.',
	},
	PDF_CV_UPLOAD: {
		label: 'PDF CV upload',
		tooltip:
			"Coming soon — upload your PDF CV and we'll extract and convert it to JSON automatically using AI.",
	},
	GENERATE_PAGE_SCOPE: {
		label: 'MVP',
		tooltip:
			'One-off CV generation, outside the HITL review pipeline. For the full pipeline (filter → review → manual apply), use LinkedIn search from Settings.',
	},
	V1_BETA: {
		label: 'v1 beta',
		tooltip:
			'Early release. WIP surfaces: auto-apply, history view, PDF CV upload. Backend records every decision; UI catches up next.',
	},
	DEEPSEEK_GROK_PICKER: {
		label: 'Server config only',
		tooltip:
			'DeepSeek and Grok work if their API keys are configured server-side. They cannot be selected here because the browser cannot validate the key.',
	},
} as const;

export type WIPFeature = keyof typeof WIP;
