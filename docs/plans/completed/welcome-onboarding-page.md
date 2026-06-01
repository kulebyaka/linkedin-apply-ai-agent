# Feature Specification: Welcome / Onboarding Page

## Overview
- **Feature**: Welcome & Onboarding Page
- **Status**: Draft
- **Created**: 2026-04-13
- **Author**: User + Claude Code

## Problem Statement

New users land on the HITL review queue (`/`) which immediately shows an empty state. There is no guidance on what the app does, how the pipeline works, or what setup steps are required before the system can produce meaningful results (uploading a CV, configuring search preferences, enabling the scheduler). This creates a silent onboarding failure — users don't know what to do first.

## Goals & Success Criteria

- New users understand the full job application pipeline before interacting with the UI
- New users are directed to complete the required setup steps (CV upload + search preferences) immediately after signing in
- Existing users can revisit the guide at any time
- **Success Metrics**: New users navigate to Settings within one session of their first login; zero "how do I start?" support questions

## User Stories

1. As a **new user**, I want to see what the app does and how the pipeline works, so that I know what to expect when I start using it.
2. As a **new user**, I want a checklist of setup steps with links, so that I know what to configure before the system starts finding jobs.
3. As a **returning user**, I want to revisit the guide from the nav, so that I can refresh my memory on keyboard shortcuts or pipeline steps.
4. As a **unauthenticated visitor**, I want to see the onboarding page before signing up, so that I can evaluate whether the tool suits my needs.

## Functional Requirements

### Core Capabilities

- `/welcome` route renders a single scrollable page, publicly accessible (no auth guard)
- Page is added to the top nav as **"Guide"** link, visible only when authenticated
- After magic link verification (`/auth/verify`), if the authenticated user has `master_cv_json == null`, redirect to `/welcome` instead of `/`
- The page contains four sections (in scroll order):
  1. **Hero** — app name, tagline, one-sentence value proposition
  2. **How It Works** — vertical timeline of the full pipeline
  3. **Feature Highlights** — cards grid of key capabilities
  4. **Quick Start** — static numbered list with links to relevant pages
- Bottom of page: primary CTA button → **"Go to Settings →"** (links to `/settings`)

### User Flows

#### First-time login flow
```
User clicks magic link
  → /auth/verify verifies token, sets JWT cookie
  → auth store loads user via /api/auth/me
  → if user.master_cv_json is null → goto('/welcome')
  → else → goto('/')
```

#### Returning user flow
```
User authenticates normally → goto('/')
Nav bar shows "Guide" link → click → /welcome
Scroll page → click "Go to Settings →" or navigate away
```

#### Unauthenticated visitor flow
```
User visits /welcome directly (no auth cookie)
Page renders fully (public route, no redirect to /login)
CTA "Go to Settings →" links to /settings (which will redirect to /login if unauthed)
```

### Section Details

#### Section 1: Hero
- Large heading: **"Your AI Job Application Agent"**
- Subheading: short paragraph explaining what the tool does (auto-scrapes LinkedIn, tailors CVs with AI, lets you review before applying)
- Decorative badge or status indicator (neo-brutalist style)

#### Section 2: How It Works — Vertical Timeline
Seven steps, each with: numbered badge, step name, 1–2 sentence description, and a status tag where relevant.

| # | Step | Description |
|---|------|-------------|
| 1 | **Job Source** | Jobs arrive via direct URL submission, manual input, or automated hourly LinkedIn scraping (when scheduler is enabled) |
| 2 | **LLM Filter** | Each job is scored 0–100 by an AI model. Jobs below the reject threshold are discarded; borderline jobs surface warning badges in the review UI |
| 3 | **CV Composition** | The AI tailors your master CV to the specific job description, emphasising the most relevant skills and experience |
| 4 | **PDF Generation** | A professional PDF resume is generated from the tailored CV JSON using WeasyPrint + Jinja2 templates |
| 5 | **HITL Review** | You review AI-generated CVs in a Tinder-style interface: Approve, Decline, or ask the AI to Retry with your feedback |
| 6 | **Application** | Approved jobs are queued for automated LinkedIn Easy Apply (coming soon) or flagged for manual application |
| 7 | **History** | All decisions are recorded so you can track your application pipeline at a glance |

#### Section 3: Feature Highlights — Cards Grid (2×3 or 3×2)

| Feature | Description |
|---------|-------------|
| **Multi-LLM Support** | Switch between OpenAI, Anthropic, DeepSeek, or Grok via a single env variable |
| **Smart Filtering** | Detect hidden disqualifiers (visa requirements, experience minimums) before wasting a tailored CV |
| **Per-User CVs** | Your master CV is stored securely in your account; every generated PDF lives in your private directory |
| **Keyboard-Driven Review** | ← → to navigate, 1 decline, 2 retry, 3 approve — never touch the mouse in the review queue |
| **Scheduled Scraping** | Set search keywords, location, and filters once; the agent fetches fresh jobs every hour |
| **Retry with Feedback** | Not happy with the generated CV? Tell the AI what to fix and it regenerates on the spot |

#### Section 4: Quick Start — Numbered List

1. **Upload your master CV** → [Settings](/settings) — paste your full work history as structured JSON
2. **Configure search preferences** → [Settings → Search Preferences](/settings) — keywords, location, remote filter, experience level
3. **Set filter preferences** → [Settings → Filter Preferences](/settings) — describe what jobs to reject in plain language
4. **Wait for the first scrape** or **submit a job manually** → [Generate](/generate)
5. **Review your first CV** → [Review](/review) — approve, decline, or retry with feedback

### Data Model

No new backend models are required. The first-login redirect logic reuses the existing `User` model field:

```typescript
// In /auth/verify page, after auth.checkAuth():
if (auth.user?.master_cv_json == null) {
  goto('/welcome');
} else {
  goto('/');
}
```

The `master_cv_json` field is already exposed via `GET /api/auth/me` → `User` model.

### Integration Points

| System | Integration |
|--------|-------------|
| `/auth/verify` route | Add post-auth redirect logic: check `master_cv_json`, branch to `/welcome` or `/` |
| `+layout.svelte` | Add "Guide" nav link; add `/welcome` to `publicPaths` array so it's not auth-guarded |
| `auth.svelte` store | No changes needed — `auth.user` already exposes `master_cv_json` |
| `+page.svelte` (new) at `/welcome` | New route file, standalone scrollable page, no store dependencies |

## Technical Design

### Architecture

Single new SvelteKit route at `ui/src/routes/welcome/+page.svelte`. No store required — purely presentational/static content. Minor changes to two existing files:

1. `ui/src/routes/+layout.svelte` — add `/welcome` to `publicPaths`; add "Guide" nav link
2. `ui/src/routes/auth/verify/+page.svelte` — add post-auth redirect branch checking `master_cv_json`

### Technology Stack

- **Framework**: SvelteKit (Svelte 5, existing)
- **Styling**: TailwindCSS + existing neo-brutalist design tokens (existing)
- **Fonts**: JetBrains Mono (headings), DM Sans (body) — already loaded
- **Icons**: Inline SVG — consistent with existing components
- **Design skill**: `frontend-design` — used during implementation for high-quality UI

### Data Persistence

No new persistence. "First login" is detected via `user.master_cv_json == null` (existing DB field, zero backend changes).

### API / Interface Design

No new API endpoints. The `/welcome` page is fully static content. The redirect logic in `/auth/verify` uses the existing `auth.user` reactive state.

```
Route: GET /welcome
Auth: Public (no guard)
Data: None — static content
```

## Non-Functional Requirements

- **Performance**: Page must render without any API calls; all content is static markup
- **Security**: Public route — no sensitive data displayed; CTA links to auth-protected pages naturally
- **Responsiveness**: Must work on mobile (single column) and desktop (grid layout for feature cards)
- **Design Consistency**: Must match the existing neo-brutalist design system — `shadow-brutal`, `border-4 border-[var(--color-foreground)]`, amber accents, monospace headings

## Implementation Considerations

### Design Trade-offs

| Decision | Chosen | Rationale |
|----------|--------|-----------|
| First-login detection | `master_cv_json == null` | Zero DB migration; reliable proxy for "user hasn't set up yet"; already available in auth store |
| Auth guard | Public | Allows potential users to preview the app before signing up; no sensitive data on the page |
| State | No persistence | Static numbered list avoids localStorage complexity for trivial benefit |
| Redirect logic location | `/auth/verify` page | Auth verify is the natural post-login checkpoint; keeps `+layout.svelte` simpler |

### Dependencies

- No new npm packages
- No backend changes
- No DB migrations

### Testing Strategy

- Manual: visit `/welcome` unauthenticated → renders without redirect
- Manual: create a new user (no CV) → magic link verify → confirm redirect to `/welcome`
- Manual: existing user with CV → magic link verify → confirm redirect to `/`
- Manual: authenticated user clicks "Guide" in nav → `/welcome` renders correctly
- Manual: click "Go to Settings →" → reaches `/settings` (or `/login` if unauthed)
- Visual: review all sections on mobile viewport (375px) and desktop (1280px)

## Out of Scope

- Interactive checkboxes / localStorage tracking of onboarding steps
- Inline wizard (collecting CV / prefs directly on the welcome page)
- Keyboard shortcut reference section (covered by the review page footer hint)
- Video or animated demo
- i18n / localization
- Analytics events on CTA clicks

## Open Questions

- Should "Guide" appear in nav for unauthenticated users? (Currently: nav is hidden for unauthenticated users entirely — no change needed)
- Should the `GET /settings` CTA scroll to the CV upload section directly via hash anchor? (Nice-to-have, deferred)

## References

- `ui/src/routes/+layout.svelte` — nav bar and `publicPaths`
- `ui/src/routes/auth/verify/+page.svelte` — post-auth redirect logic
- `ui/src/routes/login/+page.svelte` — neo-brutalist form reference
- `ui/src/app.css` — design tokens (colors, shadows, fonts)
- `src/models/user.py` — `User.master_cv_json` field
- `CLAUDE.md` — full pipeline description and architecture overview
- `frontend-design` skill — for implementation of the UI
