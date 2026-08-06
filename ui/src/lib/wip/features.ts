export const WIP = {
	AUTO_APPLY: {
		label: 'Auto-Apply',
		tooltip:
			"Auto-apply isn't built yet — nothing is ever submitted on your behalf. Use “Mark Reviewed + Open in LinkedIn” to record your review and apply by hand.",
	},
	GENERATE_PAGE_SCOPE: {
		label: 'MVP',
		tooltip:
			'One-off CV generation, outside the HITL review pipeline. For the full pipeline (filter → review → manual apply), use LinkedIn search from Settings.',
	},
	V1_BETA: {
		label: 'v1 beta',
		tooltip:
			'Early release. Everything up to the apply step works: scraping, filtering, CV tailoring, review, and history. The one gap is auto-apply — you apply manually on LinkedIn.',
	},
} as const;

export type WIPFeature = keyof typeof WIP;
