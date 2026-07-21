# Feature Specification: SDUI Easy Apply Rework

## Overview
- **Feature**: Adapt the deterministic Easy Apply automation to LinkedIn's new Server-Driven UI (SDUI) apply flow
- **Status**: Draft (interview defaults assumed — see Open Questions)
- **Created**: 2026-07-04
- **Author**: User + Claude Code

## Problem Statement

The deterministic, no-LLM Easy Apply happy path (PR #44) was built against LinkedIn's
**old artdeco modal** (`.jobs-easy-apply-modal` / `[role="dialog"]`, stable classes,
`aria-label`s tied to question text). LinkedIn has since migrated Easy Apply to a
**Server-Driven UI (SDUI)** flow, and the automation no longer works end-to-end.

Verified live this session (job `4430886894`, Wrike "Senior CloudOps Engineer"):

- **Launch control** is now an `<a aria-label="Easy Apply to this job">` with obfuscated
  hashed classes (`_3bc34f41 …`); the old `button.jobs-apply-button` is gone. Its href is
  `…/jobs/view/<id>/apply/?openSDUIApplyFlow=true`. *(Already fixed this session:
  `openEasyApply()` now matches the `<a>` by aria-label and polls for lazy render — it
  successfully clicks and the modal opens.)*
- **The apply modal** that opens ("Apply to <Company>", progress %, "Application powered by
  Greenhouse") is **NOT** `[role="dialog"]` and **NOT** `.jobs-easy-apply-modal`. Its
  container exposes no stable role/class/data-attribute. → `getModal()` returns nothing →
  the workflow fails at **"Easy Apply modal did not open."**
- **Form fields** are React-rendered: inputs carry `useId()`-generated ids like `«r0»`,
  `«r4»` (non-deterministic across renders) and have **no `name` attributes**. The current
  `serializeForm()` → `cssSelectorFor()` produces id-based selectors that are unstable and
  cannot survive the read→fill RPC gap.
- **Footer controls** (e.g. "Next") are stable only by `aria-label`.

Net effect: even with the launch button fixed, the modal-detection, form-serialization, and
Next/Review/Submit stages are all stale for the SDUI flow.

## Goals & Success Criteria
- Drive a real SDUI Easy Apply application end-to-end (open → fill known fields → advance →
  submit → confirm) with **no LLM** and **no guessing** — same philosophy as PR #44.
- Preserve every safety invariant of the current flow: unrecognized field → clean discard +
  `manual_required` (never guess), per-app timeout, daily-limit stop, mutation gate.
- Keep field references robust against React re-renders (unstable ids).
- **Success metrics**:
  - A real Easy Apply job reaches `applied` (or `manual_required` for genuinely unknown
    fields) — not `failed` at "modal did not open".
  - `APPLY_DRY_RUN=true` fills the SDUI form and stops before Submit, producing the
    pre-submit screenshot artifact.
  - Fixture-based unit tests for SDUI serialize/fill/classify pass in `extension/tests`.

## User Stories
1. As a user who approves a LinkedIn Easy Apply job, I want the extension to fill and submit
   the SDUI apply form on my behalf, so that I don't apply manually.
2. As a user whose job has a screening question the agent can't answer, I want it to abort
   cleanly and park the job in `manual_required` (with the questions captured), so I can
   answer in-app and re-apply — never a wrong auto-answer.
3. As a developer, I want the SDUI DOM handling covered by fixtures, so LinkedIn DOM churn is
   caught by tests rather than only in production.

## Functional Requirements

### Core Capabilities
- **Modal classification**: detect whether the opened apply surface is the SDUI modal or the
  legacy artdeco modal, and route to the matching handler.
- **SDUI modal detection**: a stable anchor for the SDUI modal that does not rely on
  `role="dialog"`/`.jobs-easy-apply-modal`. Candidate anchors observed: the "Apply to
  <Company>" heading, the progress bar, the "Application powered by …" footer text, and the
  aria-labelled "Next"/"Submit"/"Review" controls. Use a resilient combination.
- **Form serialization** for the SDUI modal: extract each field's *label* (via `label[for]`,
  `aria-labelledby`, `aria-label`, or nearest preceding text), `kind`, current value,
  options (for select/radio/listbox), and required flag — **without** depending on `id`/
  `name`.
- **Label-keyed fill**: fill fields by re-resolving them at fill time by label/text, so an
  unstable id never crosses an RPC boundary (see Architecture).
- **Step navigation**: click the SDUI "Next"/"Review" control by aria-label; detect the final
  "Submit application" control; detect the post-submit "Application sent" confirmation.
- **Clean abort**: dismiss/discard the SDUI modal (its own close/X and any "Discard"
  confirmation), release the mutation gate, and park `manual_required` with `pending_questions`.

### User Flows

**Happy path (per step):**
1. `open_easy_apply`: click Easy Apply `<a>` (done), poll for the SDUI modal, dismiss any
   safety-reminder. Verify modal open via the new anchor.
2. `fill_step` (≤10 iterations): `read_form_state` (SDUI serialization) → classify each field
   via `field_classifier` → if any `unknown_fields` → discard + `manual_required` → else send
   a **label-keyed fill plan** (single round-trip fill), then click "Next"/"Review".
3. `submit`: click "Submit application", capture confirmation ("Application sent"), screenshot.
4. `finalize`: persist terminal state (respecting `ALLOWED_TRANSITIONS`).

**Dry-run:** identical up to the submit step; `submit_node` (when `APPLY_DRY_RUN=true`)
snapshots the filled form, discards, and parks `manual_required` — already implemented.

### Data Model
No new persisted models. The in-flight field shape (content-script → server) is extended so
fills are label-keyed rather than selector-keyed:

```
Field = {
  key: str,          # stable per-render handle (e.g. label-hash + index) for logging only
  label: str,        # authoritative match key (normalized)
  kind: 'text'|'email'|'tel'|'number'|'file'|'checkbox'|'radio'|'select'|'listbox',
  value: str,
  options: list[str],
  required: bool,
}
FillPlan = list[{ label: str, value: str, kind: str }]   # server → content script
```

### Integration Points
- `field_classifier` (`src/services/linkedin/field_classifier.py`) — unchanged; still maps
  label → profile value, `Unknown ⇒ abort`. It already consults `ApplyProfile.custom_answers`.
- `application_workflow.py` `fill_step_node` — switches from per-field `fill_field` calls to a
  single `fill_step`/label-keyed fill per step (or keeps `fill_field` re-resolving by label).
- `apply_bridge.py` — new/adjusted tool methods for SDUI (`read_form_state`, `fill_step`,
  `advance_step`, `submit_form`, `discard`) that speak the label-keyed protocol.
- WS relay / extension protocol — new content-script primitive(s) for SDUI; existing RPC
  envelope unchanged.

## Technical Design

### Architecture
- **Detect + branch (both modals).** `getModal()` becomes a classifier returning
  `{kind: 'sdui'|'legacy'|null, root}`. Legacy handlers stay as a fallback for partial
  rollout; SDUI handlers are new. A single seam so either can be removed later.
- **Read + fill in one round-trip.** The server sends a label-keyed `FillPlan`; the content
  script re-resolves each field by normalized label/text within that one call and fills it.
  No CSS selector is ever sent from server to extension, so a React re-render between read and
  fill cannot invalidate a reference. This is the key robustness change vs the current
  selector-passing protocol.
- **Label extraction** order of precedence: `label[for=id]` → `aria-labelledby` → `aria-label`
  → nearest preceding heading/text node within the field's group container.

### Technology Stack
- **Extension**: vanilla JS content script (`extension/content_script.js`), MV3.
- **Backend**: LangGraph application workflow, `ApplyBridge` over `WsRelay`.
- **Tests**: node's built-in test runner + captured HTML fixtures (mirrors
  `extension/tests/content_script.test.mjs`).

### Data Persistence
None new. Terminal states persisted as today via `finalize_node` + `ALLOWED_TRANSITIONS`.

### API / Interface Design
- Content-script primitives (new/updated): `serialize_form` (SDUI-aware),
  `fill_step(plan)` (label-keyed, single round-trip) **or** `fill_field(label, value)`
  (re-resolve by label), `advance_step`, `submit_form`, `discard`, `open_easy_apply` (done).
- `EASY_APPLY_SELECTORS` (`easy_apply_selectors.py`) gains SDUI anchors (modal, footer-by-
  aria-label, confirmation text) alongside the legacy ones.

## Non-Functional Requirements
- **Performance**: per-app wall-clock timeout (`apply_per_app_timeout_seconds`) unchanged;
  add short polls (≤5s) for lazily-rendered SDUI surfaces (modal, next step).
- **Security**: never navigate off LinkedIn (host guard already server- and extension-side);
  mutation gate stays closed until `begin_session`.
- **Observability**: log the classified modal kind, per-step field labels + classifier
  decisions, and the abort reason. Keep the dry-run pre-submit screenshot.
- **Error Handling**: unknown field / stale anchor / validation error → `_safe_discard` +
  `manual_required`; modal never opens → `failed`; bridge drop → `needs_extension`.

## Implementation Considerations

### Design Trade-offs
- **Label-keyed fill vs selector-passing**: label-keyed is more robust to React re-renders and
  removes the stale-selector class of bug, at the cost of reworking the fill protocol and
  relying on good label extraction. Chosen because unstable `useId` ids make selectors
  fundamentally unreliable across the read→fill gap.
- **Both-modal detection vs SDUI-only**: keeping legacy as fallback costs some dead code but
  de-risks a partial LinkedIn rollout; revisit removal once SDUI is confirmed universal.

### Dependencies
- Requires a logged-in LinkedIn session in the CDP debug browser for live validation (Chrome
  149 needs the extension Load-unpacked once; see the chrome149 + sdui memories).
- ATS variance: the modal is "powered by Greenhouse" here; other ATS providers may render
  different field structures — capture multiple fixtures.

### Testing Strategy
- **Fixtures**: capture real SDUI modal HTML for ≥2 jobs (ideally different ATS) into
  `extension/tests/fixtures/`; unit-test serialization (labels/kinds/options), classification,
  and label-keyed fill resolution.
- **Live smoke**: one `APPLY_DRY_RUN=true` run against a real Easy Apply job, observed via CDP,
  reaching the pre-submit snapshot.
- Keep existing legacy-modal tests green.

## Out of Scope
- LLM-driven answering of non-deterministic screening questions (still the deferred LLM sprint).
- Vision fallback for non-LinkedIn ATS pages.
- Fixing the scraper's Easy Apply **badge** detection (0/51 jobs flagged) — tracked separately
  in `docs/plans/scraper-easy-apply-badge-fix.md`.
- Any change to `field_classifier` matching logic.

## Open Questions
The following were interview questions the user had not answered when this draft was written;
current choices are the recommended defaults and should be confirmed:
1. **Scope** — assumed *Happy path + clean abort* (contact info, file upload, recognized
   fields, multi-step submit; unknown → `manual_required`). Alternatives: full edge-case
   parity, or contact-info smoke only.
2. **Compatibility** — assumed *detect + branch (both modals)*. Alternative: SDUI-only.
3. **Field references** — assumed *read + fill in one round-trip (label-keyed)*. Alternatives:
   re-resolve by label with the current RPC split, or stable structural selectors.
4. **Testing** — assumed *DOM fixtures + live smoke*.
5. Which ATS variants to capture as fixtures (Greenhouse confirmed; others?).

## References
- `extension/content_script.js` (`openEasyApply` fixed; `getModal`, `serializeForm`,
  `fillField` to rework)
- `src/services/linkedin/easy_apply_selectors.py`, `src/services/linkedin/apply_bridge.py`
- `src/agents/application_workflow.py` (`open_easy_apply_node`, `fill_step_node`,
  `submit_node`; `APPLY_DRY_RUN` added this session)
- `src/services/linkedin/field_classifier.py`
- Memory: `linkedin-sdui-easy-apply-migration`, `chrome149-load-extension-dead`
- Prior: `docs/plans/completed/easy-apply-happy-path.md`, PR #44
