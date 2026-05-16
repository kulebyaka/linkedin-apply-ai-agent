# Feature Specification: VPS Launch UX Audit — Mark "Work in Progress" Surfaces

## Overview

- **Feature**: UX audit + WIP markers across UI for VPS launch
- **Status**: Draft
- **Created**: 2026-05-16
- **Author**: User + Claude Code
- **Scope**: Pure UX surface treatment. Complements (does not supersede) `vps-deployment.md` (infra) and `v1-launch-fixes.md` (functional gaps). Where this spec and `v1-launch-fixes.md` overlap on the same control (notably Approve and the "v1 beta" badge), this document is authoritative for the *visual treatment*; the functional fixes in `v1-launch-fixes.md` remain authoritative for backend/state behavior.

## Problem Statement

The product is being prepared for a VPS launch to a small group of real users (3–5 friends per `v1-launch-fixes.md`). Several user-visible surfaces in the UI advertise or appear to expose features that are not actually implemented:

- The `Approve` button on the HITL review queue mutates state but triggers no downstream automation (`src/services/jobs/hitl_processor.py:191-199`).
- The `welcome` page advertises step **06 Application** ("Approved jobs are queued for automated LinkedIn Easy Apply") and step **07 History** as if they are live, but the application workflow is stubbed and there is no History view in the UI.
- The CV upload section shows a "Coming soon" PDF upload (already disabled with a hover tooltip — the precedent pattern we should follow elsewhere).
- The `JobDescriptionForm` provider dropdown lists OpenAI and Anthropic only, while CLAUDE.md claims four-provider support — minor inconsistency, not user-blocking.
- The `Generate` page lets the user pick provider/model per-submission, which works, but it sits next to a HITL flow whose Approve step is inert — the disconnect needs surfacing.
- The `URL` and `Manual` job-source adapters raise `NotImplementedError` (`src/services/jobs/job_source.py:120-125,160-166`); only `LinkedIn` (via scheduler) and the inlined "manual" path used by `/generate` actually work. The UI never exposes URL submission, so this is invisible — keep it that way.

A first-time user on the deployed VPS today would click `Approve`, get a successful-looking toast, and reasonably expect an application to follow. It will not. Before sharing the URL, every surface that promises an unimplemented capability must visibly signal its WIP status.

## Goals & Success Criteria

- Every UI control that *appears* functional but invokes stubbed/unimplemented logic is either **disabled with a tooltip** or **clearly labelled WIP**. The user can never click a control expecting result X and get silence.
- A first-time user on the VPS can walk the golden path (login → settings → CV → search prefs → start search → review → decline/retry) without encountering a misleading affordance.
- The `welcome` page accurately reflects which pipeline steps are live vs. WIP.
- Every WIP marker links the user to a single, consistent rationale: "this is a v1 limitation; manual application via the LinkedIn URL is the workaround."
- No backend behavior changes. This is a UI/copy/CSS pass only.
- **Success Metrics**:
  - Manual smoke test: zero misleading "success" toasts on the WIP path.
  - All disabled controls have a hover tooltip rendered within 200ms.
  - The welcome page's pipeline list distinguishes "live" from "WIP" steps with the same visual vocabulary.

## User Stories

1. As a first-time user, I want to see immediately which features in the UI are not yet implemented, so that I can plan around them and not waste time clicking dead buttons.
2. As a first-time user reading the welcome page, I want the "How it works" timeline to honestly distinguish what the agent will do for me from what is still on the roadmap, so I form correct expectations.
3. As the maintainer, I want every WIP marker to share the same visual style, so that the launch feels deliberate rather than half-finished.
4. As the maintainer, I want WIP markers backed by component-level constants — not hand-edited copy scattered through routes — so I can flip a feature from WIP to live by editing one map.

## Functional Requirements

### Core Capabilities

- **Single source of truth** for WIP flags: a small TypeScript module exporting feature flags + canonical copy (e.g. `ui/src/lib/wip/features.ts`).
- **Two visual primitives**, both already present as ad-hoc patterns in the codebase, formalized as reusable components:
  - `<WIPButton>` — wraps a `<button disabled>` with a hover tooltip slot. Style mirrors the existing "Upload PDF CV" pattern at `ui/src/lib/components/settings/CVUploadSection.svelte:254-268` (muted border, opacity 50, `cursor-not-allowed`, tooltip on `group-hover`).
  - `<WIPBadge>` — small label rendered next to a feature name (e.g. inline `WIP` pill). Style consistent with the existing tag system in welcome's `tagStyle` map (`ui/src/routes/welcome/+page.svelte:136-142`).
- **No new toasts or banners** introduced solely for WIP messaging — keep the surface quiet. The badge + tooltip pair is the entire vocabulary.

### Surfaces to Mark WIP (Full Audit)

The table below enumerates every WIP surface found in the audit. Each row is a discrete UI change.

| # | Surface | File | Current state | Treatment | Rationale |
|---|---------|------|---------------|-----------|-----------|
| U1 | **Approve button** in HITL review | `ui/src/lib/components/review/DecisionButtons.svelte:56-68` | Active, mutates DB state, shows generic success toast. | Replace with `<WIPButton>` styled identically (green border + check icon) but disabled, tooltip: *"Auto-apply is coming soon. For now, approve marks the job as reviewed — open the job in LinkedIn to apply manually."* Add an adjacent secondary action `Mark Reviewed + Open in LinkedIn ↗` that does what Approve currently does AND opens `job.application_url` in a new tab. | Today Approve is the most misleading control in the app. `v1-launch-fixes.md` Task 3 wants a "Coming soon" *toast*; this spec prefers a disabled button so the user never expects an apply to start. |
| U2 | **Welcome — pipeline step 06 "Application"** | `ui/src/routes/welcome/+page.svelte:48-54` | Step card describes Easy Apply automation as if live, tagged `AI`. | Append a `<WIPBadge>` inline with the step name. Update copy: *"Auto-application is on the roadmap — for v1 you'll apply manually via the LinkedIn link surfaced in the review queue."* | Welcome page is the user's mental model — it must not lie. |
| U3 | **Welcome — pipeline step 07 "History"** | `ui/src/routes/welcome/+page.svelte:55-62` | Step card describes a history log, but no `/history` route exists. | Append `<WIPBadge>`. Update copy: *"History view is on the roadmap — the API records every decision today (`GET /api/hitl/history`), the UI surfaces it next."* | The API endpoint exists per `CLAUDE.md`, but no Svelte route consumes it. |
| U4 | **Welcome — quick-start step 04 "Submit a job manually"** | `ui/src/routes/welcome/+page.svelte:122-127` | Links to `/generate`. The page works but its product role is unclear post-LinkedIn-scheduler — and its Approve flow is the inert one from U1. | Keep working; reorder so step 04 is **"Trigger your first LinkedIn search"** linking to `/settings` (the `StartSearchSection`). Move "Submit a job manually" to step 05 as an optional alternative. | Aligns onboarding with the actually-live primary path. |
| U5 | **JobDescriptionForm provider dropdown** | `ui/src/lib/components/JobDescriptionForm.svelte:33-48` | Lists OpenAI + Anthropic only; CLAUDE.md mentions DeepSeek + Grok too. | Leave OpenAI + Anthropic in the dropdown. Add a single grayed-out option `Deepseek / Grok — server config only` that is `disabled` in the `<option>` element. | DeepSeek/Grok work if env vars are set on the server, but the per-submission picker has no way to validate the key from the browser — making them selectable here would invite confusing 500s. |
| U6 | **`/generate` page header copy** | `ui/src/routes/generate/+page.svelte:158-167` | Header says "Paste a job description and get a tailored CV PDF instantly". Does not mention the page does NOT enter the HITL review queue (it's the MVP path). | Append a `<WIPBadge>` labelled `MVP` to the header with tooltip: *"This page generates a one-off CV PDF and skips the review queue. For the full pipeline (filter → review → manual apply), use LinkedIn search from Settings."* | The page is functional but operates outside the HITL flow described on welcome. Users currently can't tell. |
| U7 | **Nav link "Generate"** | `ui/src/routes/+layout.svelte:41-46` | Equal weight with "Review" and "Settings". | Add a `<WIPBadge>` `MVP` after the label (small, muted). | Same reason as U6 — secondary surface needs the same hint. |
| U8 | **Application URL on JobCard / CVPreview** | `ui/src/lib/components/review/JobCard.svelte` + descendants | The `application_url` is shown but its role isn't framed as the user's apply path. | Reframe the link as the primary CTA inside `JobCard`: a button-styled link reading `Apply on LinkedIn ↗`. Place it adjacent to the DecisionButtons. | If Approve is disabled per U1, the user needs the manual apply path obvious. |
| U9 | **Top-bar version/beta marker** | `ui/src/routes/+layout.svelte:59-62` (nav bar) | "Job Application Agent" text only. | Append a small `v1 beta` pill next to the product name. On hover, tooltip lists current WIP surfaces (auto-apply, history view, PDF CV upload). | This is the `v1-launch-fixes.md` Task 3 "v1 beta badge" — implemented here in the visual vocabulary defined by this spec. |
| U10 | **CV PDF upload button** | `ui/src/lib/components/settings/CVUploadSection.svelte:254-268` | Already disabled with hover tooltip. ✓ | No change — this is the reference pattern. Refactor inline into `<WIPButton>` as part of Task 2 below. | Don't break what works; standardise it. |
| U11 | **Welcome stat "∞ jobs/hour"** | `ui/src/routes/welcome/+page.svelte:240-248` | Aspirational; without an apply step, "jobs/hour" overstates capability. | Replace the third stat tile from `∞ jobs/hour` to `1 user, today` (or similar honest copy). Style unchanged. | Don't promise scale we don't deliver in v1. |
| U12 | **Inert URL job-source path** | `src/services/jobs/job_source.py:120-125,160-166` (URL adapter `NotImplementedError`) | Never exposed in the UI. | **No UI change.** Explicitly noted here so future contributors do not add a "Paste URL" input that would hit the broken adapter. Add an inline TODO comment in `job_source.py` linking back to this spec. | Documenting the invisible WIP is part of the audit. |

### User Flows

**Happy path (changed by this spec):**

1. New user logs in → lands on `/` (HITL review queue) which is empty.
2. Sees nav `v1 beta` badge and clicks `Settings`.
3. Uploads master CV (JSON), configures search prefs.
4. Clicks `Start LinkedIn Search` (in `StartSearchSection` — already live, no change).
5. Returns to `/`. As jobs land, the user reviews:
   - **Decline** and **Retry** work normally.
   - **Approve** is disabled with the tooltip from U1.
   - The adjacent `Mark Reviewed + Open in LinkedIn ↗` action transitions the job to `approved` (existing backend behavior) AND opens the LinkedIn job URL in a new tab where the user applies manually.
6. No misleading success state at any point.

**Walking the welcome page:**

1. User scrolls; pipeline steps 06 and 07 are clearly marked `WIP`.
2. Quick-start ordering reflects the actually-live primary path (LinkedIn search first, manual generate second).

### Integration Points

- All changes are UI-only files in `ui/src/`. No backend changes.
- Two new components: `ui/src/lib/components/wip/WIPButton.svelte`, `ui/src/lib/components/wip/WIPBadge.svelte`.
- One new module: `ui/src/lib/wip/features.ts` — feature flag + copy registry (single map, ~10 entries).
- One backend-adjacent comment-only edit: `src/services/jobs/job_source.py` gets a TODO comment near the `NotImplementedError` blocks pointing at this spec. No code change.

### Data Model

No new data models. The WIP registry is a frozen object literal:

```ts
// ui/src/lib/wip/features.ts
export const WIP = {
  AUTO_APPLY: {
    label: 'Auto-Apply',
    tooltip: "Auto-apply is coming soon. Approve marks the job as reviewed — open it in LinkedIn to apply manually.",
  },
  HISTORY_VIEW: {
    label: 'History view',
    tooltip: 'The API records every decision; the UI surface is next.',
  },
  PDF_CV_UPLOAD: {
    label: 'PDF CV upload',
    tooltip: "Coming soon — we'll extract and convert PDF CVs to JSON automatically.",
  },
  GENERATE_PAGE_SCOPE: {
    label: 'MVP',
    tooltip: 'One-off CV generation, outside the HITL review pipeline.',
  },
} as const;
```

## Technical Design

### Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      ui/src/lib/wip/                              │
│  features.ts  ──────────► single source of WIP copy + flags       │
└────────────────────┬─────────────────────────────────────────────┘
                     │
       ┌─────────────┼──────────────┐
       ▼             ▼              ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────────────┐
│ WIPButton    │ │ WIPBadge     │ │ Direct imports in    │
│ .svelte      │ │ .svelte      │ │ +layout, welcome,    │
└──────────────┘ └──────────────┘ │ DecisionButtons,     │
                                  │ generate, JobCard    │
                                  └──────────────────────┘
```

### Technology Stack

- Svelte 5 (runes), Tailwind v4 — already in use.
- No new dependencies.
- Components use the existing "brutalist" visual language (`border-2 border-[var(--color-foreground)]`, `shadow-brutal`, `font-mono` for labels).

### API / Interface Design

**`<WIPButton>` API:**

```svelte
<script lang="ts">
  interface Props {
    label: string;
    tooltip: string;
    variant?: 'default' | 'success' | 'destructive';
    icon?: import('svelte').Snippet;
  }
  let { label, tooltip, variant = 'default', icon }: Props = $props();
</script>

<div class="group relative inline-block">
  <button disabled class="...muted variant styles...">
    {#if icon}{@render icon()}{/if}
    {label}
  </button>
  <div class="pointer-events-none absolute bottom-full ...hidden group-hover:block...">
    <p class="font-mono text-xs">{tooltip}</p>
  </div>
</div>
```

**`<WIPBadge>` API:**

```svelte
<script lang="ts">
  interface Props {
    label?: string; // default 'WIP'
    tooltip?: string;
    size?: 'sm' | 'md';
  }
  let { label = 'WIP', tooltip, size = 'sm' }: Props = $props();
</script>

<span class="group relative inline-flex items-center ...amber pill...">
  {label}
  {#if tooltip}
    <span class="pointer-events-none absolute ...group-hover:block...">{tooltip}</span>
  {/if}
</span>
```

### Visual Specification

- **WIP color**: amber, using existing `var(--color-primary)` (already used for tags/badges in the codebase). Disabled-button variant uses `border-[var(--color-muted)]` + `opacity-50` + `cursor-not-allowed` (matches `CVUploadSection.svelte:254-268`).
- **Tooltip positioning**: above the trigger element by default (`bottom-full mb-2`), with fallback to below when near viewport top. Width capped at `w-64`. Background `bg-white`, `border-2 border-[var(--color-foreground)]`, `shadow-brutal`.
- **Hover delay**: none (instant) — Tailwind `group-hover` only, no transition delay.
- **Mobile**: tooltips become click-to-toggle via `aria-describedby` linking; tap outside dismisses. (Acceptable defer if implementation runs long — note in Open Questions.)

## Non-Functional Requirements

- **Performance**: zero runtime impact — components render statically with no JS state machinery.
- **Accessibility**:
  - `<WIPButton>` must set `aria-disabled="true"` and `title={tooltip}` so screen readers and keyboard navigation surface the rationale without hover.
  - `<WIPBadge>` must wrap its tooltip in an `aria-describedby` association.
  - Disabled controls remain focusable for keyboard discovery (use `tabindex="0"` + `aria-disabled` rather than the native `disabled` attribute alone, which hides from tab order).
- **Internationalization**: copy lives in `features.ts` — future i18n work has one file to translate.
- **Visual regression**: take before/after screenshots of `/welcome`, `/`, `/generate`, `/settings` for the PR description.
- **Error Handling**: N/A — no new failure modes introduced.

## Implementation Considerations

### Design Trade-offs

- **Disabled button vs. active button + "coming soon" toast for Approve.** `v1-launch-fixes.md` Task 3 chose toast (keeps the button visible, transitions to `approved`). This spec prefers disabled-with-tooltip because:
  - A success-styled toast after a click that does nothing feels worse than a button that visibly says "you can't do this yet."
  - The adjacent `Mark Reviewed + Open in LinkedIn ↗` action gives the user the *real* path forward, which a toast does not.
  - Trade-off: if the user genuinely wants to mark the job as `approved` *without* opening LinkedIn (e.g., they've already applied elsewhere), the secondary action's coupling of state-transition + tab-open is a small friction. Acceptable for v1; revisit when auto-apply lands.
- **Single registry vs. per-component constants.** Single registry costs one extra file but makes "flip from WIP to live" a one-line edit. Worth it for a v1 audit doc that should disappear over time.
- **No new banner / no global "v1 beta" header.** Considered a top-of-page yellow banner; rejected because the nav-bar `v1 beta` pill (U9) plus per-control markers are already saturating. A banner would feel apologetic.
- **`Mark Reviewed + Open in LinkedIn ↗` as the secondary action vs. two separate buttons.** Two buttons (one to mark-reviewed, one to open-in-linkedin) is more granular but doubles the visual weight in the decision row. Coupling them in one button is the smaller surface and matches the user's actual workflow.

### Dependencies

- No new npm packages.
- No backend changes.
- Coordinates with `v1-launch-fixes.md` Task 3 — when that task lands, its toast implementation should be removed in favor of this spec's disabled-button treatment. Suggest sequencing this spec **after** Task 3 lands, or merging the two if executed together.

### Testing Strategy

- **Manual smoke test**: walk the user flows above on the dev server and confirm every WIP marker renders, every tooltip is reachable, and the LinkedIn manual-apply path works end-to-end.
- **Component test** (Vitest + Testing Library):
  - `<WIPButton>` renders disabled, tooltip text appears in DOM (visibility tested via `:hover` selector class).
  - `<WIPBadge>` renders the correct label from a `WIP.AUTO_APPLY` import.
- **E2E test** (Playwright, extend `tests/e2e/test_hitl_review.py`):
  - Approve button is `aria-disabled="true"` and the tooltip text is present.
  - The new `Mark Reviewed + Open in LinkedIn ↗` button triggers `POST /api/hitl/{id}/decide` with `approved` AND opens a new tab to `application_url`.
- **Visual regression** (manual, screenshot diff in PR): four key routes.

## Out of Scope

- Backend behavior changes (Approve still transitions state to `approved`; no new state).
- Implementing any of the actual WIP features (auto-apply, history view, PDF CV ingestion).
- Mobile-specific tooltip UX beyond the click-to-toggle fallback noted in NFRs.
- Internationalization (beyond placing copy in a single file for future translation).
- A global "v1 beta" page banner (deliberately rejected above).
- Marking WIP in the backend (CLAUDE.md already documents Implementation Status — that table stays the developer-facing truth).
- Adding a History route (the API exists; surfacing it is a separate feature, not a WIP marker).

## Open Questions

- Should the secondary action be named `Mark Reviewed + Open in LinkedIn ↗` or split into `Open in LinkedIn ↗` (passive, no state change) + a separate `Mark Reviewed` button? Current proposal couples them; user input welcome.
- Mobile tooltip strategy: acceptable to defer to "tap-to-toggle on touch devices" implementation, or required for v1? If required, scope grows by ~half a day.
- Should `v1 beta` in the nav bar link anywhere (e.g., to a `/changelog` or a GitHub release notes page)? Today there is no such destination — leave it inert (tooltip only) for v1.
- Once `v1-launch-fixes.md` Task 3 ships, do we want to keep the "Coming soon" toast as a secondary signal alongside this spec's disabled button, or remove it entirely? Recommendation: remove, to avoid double messaging.

## References

- `docs/plans/vps-deployment.md` — infrastructure plan (GHCR + Actions + Watchtower).
- `docs/plans/v1-launch-fixes.md` — functional launch blockers; overlaps with this doc on Approve (Task 3) and the "v1 beta" badge.
- `docs/plans/linkedin-apply-chrome-extension.md` — the *real* fix for U1 (auto-apply via MV3 extension); separate roadmap item.
- `CLAUDE.md` § "Implementation Status" — the developer-facing list of what is live vs. stubbed.
- Existing precedent for the disabled-with-tooltip pattern: `ui/src/lib/components/settings/CVUploadSection.svelte:254-268`.
- Existing badge / tag visual vocabulary: `ui/src/routes/welcome/+page.svelte:136-142` (`tagStyle` map).
