# Feature Specification: MVP Web UI

## Overview
- **Feature**: MVP Web UI for Job Description → CV PDF Generation
- **Status**: Draft
- **Created**: 2026-01-04
- **Author**: User + Claude Code

## Problem Statement

The LinkedIn Job Application Agent currently requires users to interact with the backend API directly via curl/Postman or Python scripts. This creates friction for non-technical users and makes it difficult to quickly test the CV generation workflow. An MVP web UI is needed to provide a simple, user-friendly interface for submitting job descriptions and downloading tailored CV PDFs.

## Goals & Success Criteria

### Goals
1. **Eliminate technical barriers**: Allow users to generate tailored CVs without API knowledge
2. **Provide immediate feedback**: Show real-time workflow progress with clear status updates
3. **Streamline workflow**: Single-page experience from job description input to PDF download
4. **Future-proof architecture**: Build foundation for Full mode (HITL review UI) expansion

### Success Metrics
- Users can successfully generate CV PDFs in under 60 seconds (including LLM processing)
- Zero failed submissions due to client-side validation errors
- Clean, professional UI that requires no documentation to use
- Architecture supports adding HITL review features without major refactor

## User Stories

1. **As a job seeker**, I want to paste a job description and get a tailored CV PDF immediately, so that I can quickly apply to positions without manually editing my resume.

2. **As a non-technical user**, I want clear visual feedback during CV generation, so that I know the system is working and understand what step it's on.

3. **As a power user**, I want to quickly iterate on multiple job descriptions, so that I can generate CVs for several positions in one session.

4. **As a developer**, I want the UI architecture to support future HITL features, so that we don't need to rebuild when adding Full mode.

## Functional Requirements

### Core Capabilities

1. **Job Description Input**
   - Single large textarea for pasting entire job description (free-form text)
   - Minimum validation: 50 characters required
   - Clear placeholder text with example
   - Submit button (disabled until validation passes)

2. **Async CV Generation**
   - Submit job description to `/api/jobs/submit` endpoint (unified API, MVP mode)
   - Backend uses LLM to extract title, company, description, requirements from free-form text
   - Poll job status every 2 seconds via `/api/jobs/{job_id}/status`
   - Display current workflow step based on status field

3. **Progress Feedback**
   - Show loading state with step-by-step progress:
     - "Extracting job details..." (status: `extracting`)
     - "Composing tailored CV..." (status: `composing_cv`)
     - "Generating PDF..." (status: `generating_pdf`)
     - "Complete!" (status: `completed`)
   - Visual step indicators (e.g., progress stepper or animated list)

4. **PDF Download**
   - Auto-download PDF when status reaches `completed`
   - Use `/api/jobs/{job_id}/pdf` endpoint
   - Trigger browser download programmatically
   - **Fallback**: Show manual "Download PDF" button if auto-download fails or is blocked by browser
   - Handle download errors gracefully (show error toast)

5. **Error Handling**
   - Show toast notification for errors (status: `failed`)
   - Display error message from API response
   - Allow user to edit and resubmit (keep textarea content)
   - Handle network errors gracefully

6. **Reset/New Job**
   - After successful download, show "Generate Another CV" button
   - Clear textarea and reset state for new submission

### User Flows

#### Happy Path (Auto-download Success)
1. User opens app (single page)
2. User pastes job description into textarea
3. User clicks "Generate CV" button
4. UI shows loading state with "Extracting job details..." step
5. Status polling updates to "Composing tailored CV..." step
6. Status polling updates to "Generating PDF..." step
7. Status reaches `completed`, PDF auto-downloads successfully
8. Polling stops, UI shows success message: "Your CV has been generated!"
9. User clicks "Generate Another CV" to reset

#### Happy Path (Auto-download Blocked)
1. User opens app (single page)
2. User pastes job description into textarea
3. User clicks "Generate CV" button
4. UI shows loading state with progress steps
5. Status reaches `completed`, auto-download fails (browser security)
6. Polling stops, UI shows "Download PDF" button
7. User clicks "Download PDF" button manually
8. PDF downloads successfully
9. User clicks "Generate Another CV" to reset

#### Error Path
1. User opens app
2. User pastes invalid/short text
3. User clicks "Generate CV" button
4. Client-side validation fails, shows error message below textarea
5. User adds more text, validation passes
6. User clicks "Generate CV" button
7. Backend LLM fails (timeout, API error, etc.)
8. Status polling detects `failed` status
9. Toast notification appears: "CV generation failed: [error message, stack trace]"
10. User edits textarea and clicks "Generate CV" again

### Data Model

#### Client State
```typescript
type AppState = {
  // Input
  jobDescription: string;

  // Workflow state
  status: 'idle' | 'submitting' | 'polling' | 'completed' | 'failed';
  currentStep: 'queued' | 'extracting' | 'composing_cv' | 'generating_pdf' | 'completed' | 'failed';

  // Job tracking
  jobId: string | null;

  // Download state
  pdfBlob: Blob | null;  // Cached PDF blob for fallback download
  autoDownloadFailed: boolean;  // True if auto-download was blocked

  // Error handling
  errorMessage: string | null;

  // Polling control
  pollingInterval: number | null;
};
```

#### API Request/Response Types
```typescript
// POST /api/jobs/submit
interface JobSubmitRequest {
  source: 'manual';
  mode: 'mvp';
  job_description: {
    title: string;        // Extracted by LLM
    company: string;      // Extracted by LLM
    description: string;  // From textarea
    requirements: string; // Extracted by LLM
  };
}

interface JobSubmitResponse {
  job_id: string;
  status: 'queued';
  message: string;
}

// GET /api/jobs/{job_id}/status
interface JobStatusResponse {
  job_id: string;
  source: 'manual';
  mode: 'mvp';
  status: 'queued' | 'extracting' | 'composing_cv' | 'generating_pdf' | 'completed' | 'failed';
  job_posting?: {
    title: string;
    company: string;
    description: string;
    requirements: string;
  };
  pdf_path?: string;
  error_message?: string;
  retry_count: number;
  created_at: string;
}
```

### Integration Points

1. **FastAPI Backend** (`src/api/main.py`)
   - Submit job: `POST /api/jobs/submit`
   - Check status: `GET /api/jobs/{job_id}/status`
   - Download PDF: `GET /api/jobs/{job_id}/pdf`

2. **Preparation Workflow** (`src/agents/preparation_workflow.py`)
   - Executed server-side after submission
   - Frontend polls for status updates

3. **Static File Serving**
   - FastAPI serves built UI from `src/ui/build/` or `dist/`
   - Add static file mount to `main.py`

## Technical Design

### Architecture

**Framework**: SvelteKit (Svelte 5 with Runes API)
**Type Safety**: Full TypeScript
**Styling**: TailwindCSS
**Build Tool**: Vite (via SvelteKit)

#### Component Structure
```
src/ui/
├── src/
│   ├── routes/
│   │   └── +page.svelte          # Main (and only) page
│   ├── lib/
│   │   ├── components/
│   │   │   ├── JobDescriptionForm.svelte
│   │   │   ├── ProgressStepper.svelte
│   │   │   └── ToastNotification.svelte
│   │   ├── stores/
│   │   │   └── appState.svelte.ts  # Svelte 5 runes-based state
│   │   ├── api/
│   │   │   └── client.ts          # API client with typed fetch
│   │   └── utils/
│   │       ├── validation.ts      # Input validation
│   │       └── download.ts        # PDF download helper
│   ├── app.html                   # HTML template
│   └── app.css                    # Global Tailwind imports
├── static/
│   └── favicon.png
├── svelte.config.js
├── vite.config.ts
├── tailwind.config.js
├── tsconfig.json
└── package.json
```

#### State Management Pattern (Svelte 5 Runes)
```typescript
// src/lib/stores/appState.svelte.ts
import { type AppState } from '../types';

let state = $state<AppState>({
  jobDescription: '',
  status: 'idle',
  currentStep: 'queued',
  jobId: null,
  pdfBlob: null,
  autoDownloadFailed: false,
  errorMessage: null,
  pollingInterval: null,
});

export const appState = {
  get value() { return state; },

  setJobDescription(description: string) {
    state.jobDescription = description;
  },

  startPolling(jobId: string) {
    state.status = 'polling';
    state.jobId = jobId;
  },

  updateStep(step: string) {
    state.currentStep = step;
  },

  setCompleted(pdfBlob: Blob, autoDownloadSucceeded: boolean) {
    state.status = 'completed';
    state.currentStep = 'completed';
    state.pdfBlob = pdfBlob;
    state.autoDownloadFailed = !autoDownloadSucceeded;
  },

  setError(message: string) {
    state.status = 'failed';
    state.errorMessage = message;
  },

  // Clean up polling interval (called in onDestroy)
  cleanup() {
    if (state.pollingInterval) {
      clearInterval(state.pollingInterval);
      state.pollingInterval = null;
    }
  },

  reset() {
    this.cleanup();  // Clear interval before resetting
    state = {
      jobDescription: '',
      status: 'idle',
      currentStep: 'queued',
      jobId: null,
      pdfBlob: null,
      autoDownloadFailed: false,
      errorMessage: null,
      pollingInterval: null,
    };
  },
};
```

### Technology Stack

**Frontend Framework**
- **SvelteKit 2.x** (Svelte 5 with Runes)
- **TypeScript 5.x**
- **Vite 5.x** (via SvelteKit)

**Styling**
- **TailwindCSS 4.x** (latest)
- **PostCSS** (for Tailwind processing)

**HTTP Client**
- **Native fetch API** (no external dependencies)
- **TypeScript types** for request/response validation

**Build & Dev Tools**
- **@sveltejs/kit**
- **@sveltejs/adapter-static** (for static build)
- **@sveltejs/vite-plugin-svelte**
- **vite**
- **typescript**
- **tailwindcss**

**Testing** (optional for MVP, but recommended setup)
- **Vitest** (unit tests)
- **@testing-library/svelte** (component tests)
- **Playwright** (E2E tests, reuse existing Playwright setup)

### Data Persistence

**Client-Side**: No persistence required for MVP
- All state is ephemeral (resets on page refresh)
- Future enhancement: LocalStorage for job history

**Server-Side**: Existing `InMemoryJobRepository`
- No changes needed to backend persistence

### API/Interface Design

#### API Client (`src/ui/src/lib/api/client.ts`)
```typescript
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

export async function submitJob(jobDescription: string): Promise<JobSubmitResponse> {
  // Generate placeholders in case LLM extraction fails
  const timestamp = new Date().toISOString().split('T')[0]; // YYYY-MM-DD

  const response = await fetch(`${API_BASE_URL}/api/jobs/submit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      source: 'manual',
      mode: 'mvp',
      job_description: {
        title: `mvp-${timestamp}-title`,  // Placeholder if LLM fails
        company: `mvp-${timestamp}-company`,  // Placeholder if LLM fails
        description: jobDescription,
        requirements: '',  // LLM will extract or leave empty
      },
    }),
  });

  if (!response.ok) {
    throw new Error(`API error: ${response.statusText}`);
  }

  return response.json();
}

export async function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/status`);

  if (!response.ok) {
    throw new Error(`API error: ${response.statusText}`);
  }

  return response.json();
}

export async function downloadPDF(jobId: string): Promise<Blob> {
  const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/pdf`);

  if (!response.ok) {
    throw new Error(`PDF download failed: ${response.statusText}`);
  }

  return response.blob();
}

// Helper to trigger browser download
export function triggerDownload(blob: Blob, filename: string): boolean {
  try {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    return true;  // Auto-download succeeded
  } catch (error) {
    console.error('Auto-download failed:', error);
    return false;  // Auto-download blocked, need fallback button
  }
}
```

#### Component Props
```typescript
// JobDescriptionForm.svelte
interface Props {
  onSubmit: (description: string) => void;
  isLoading: boolean;
  errorMessage?: string;
}

// ProgressStepper.svelte
interface Props {
  currentStep: 'queued' | 'extracting' | 'composing_cv' | 'generating_pdf' | 'completed';
}

// ToastNotification.svelte
interface Props {
  message: string;
  type: 'error' | 'success' | 'info';
  duration?: number;  // Auto-dismiss after N ms
  onClose: () => void;
}
```

## Non-Functional Requirements

### Performance
- **Initial Load**: < 1 second (first contentful paint)
- **Bundle Size**: < 100 KB (gzipped, excluding Tailwind purge)
- **Status Polling**: Every 2 seconds (balance between responsiveness and server load)
- **Auto-download**: Trigger immediately when `completed` status detected

### Accessibility
- **Keyboard Navigation**: Full tab navigation support
- **ARIA Labels**: Proper labels for form inputs and buttons
- **Focus Management**: Clear focus indicators
- **Screen Reader Support**: Semantic HTML with descriptive labels

### Responsiveness
- **Mobile-First**: Design works on 320px+ width
- **Breakpoints**: sm (640px), md (768px), lg (1024px)
- **Textarea**: Responsive height (min 10 lines, max 25 lines)

### Security
- **No Sensitive Data**: Job descriptions are not sensitive (no auth required)
- **CORS**: Backend already configured with cors_origins
- **XSS Prevention**: Svelte automatically escapes content
- **Input Sanitization**: Basic validation only (backend handles extraction)

### Browser Support
- **Modern Browsers**: Chrome/Edge 100+, Firefox 100+, Safari 15+
- **No IE Support**: SvelteKit/Svelte 5 requires modern JS

### Observability
- **Client Logging**: Console logs for API calls (dev mode only)
- **Error Tracking**: Log errors to console (future: integrate Sentry)
- **Analytics**: None for MVP (future: Plausible/PostHog)

## Implementation Considerations

### Design Trade-offs

#### 1. **Single Textarea vs. Structured Form**
**Decision**: Single textarea with LLM extraction
**Rationale**:
- Reduces user friction (copy-paste entire job posting)
- Leverages existing backend LLM capabilities
- Users often copy full job descriptions from LinkedIn/Indeed
**Trade-off**: Less precise input, relies on LLM quality

#### 2. **SvelteKit vs. Vite + Svelte**
**Decision**: SvelteKit with static adapter
**Rationale**:
- Built-in routing (needed for future HITL pages)
- Better dev experience (HMR, file-based routing)
- Static build works with FastAPI serving
**Trade-off**: Slightly larger bundle, more complex config

#### 3. **Auto-download vs. Manual Download Button**
**Decision**: Auto-download with manual fallback button
**Rationale**:
- Seamless UX (no extra click required)
- User expects instant result after waiting
- Fallback button handles browser security blocks
**Trade-off**: Slightly more complex logic, but better resilience

#### 4. **Status Polling vs. WebSocket**
**Decision**: HTTP polling every 2 seconds
**Rationale**:
- Backend already supports polling endpoints
- Simpler implementation (no WebSocket server required)
- CV generation typically takes 10-30 seconds
**Trade-off**: Slight delay in status updates, more HTTP requests

#### 5. **Toast vs. Inline Error Messages**
**Decision**: Toast notifications for async errors
**Rationale**:
- Doesn't disrupt form layout
- User can dismiss and continue
- Standard pattern for async operations
**Trade-off**: Easier to miss than inline errors

#### 6. **Unified vs. Legacy API Endpoint**
**Decision**: Use `/api/jobs/submit` (unified endpoint)
**Rationale**:
- Future-proof for Full mode
- Aligns with new two-workflow architecture
- Action item: Remove legacy endpoints after migration
**Trade-off**: Slightly more complex request payload

#### 7. **LLM Extraction Failure Handling**
**Decision**: Use placeholder pattern `mvp-<date>-title`, `mvp-<date>-company`
**Rationale**:
- Ensures job submission succeeds even if LLM fails to extract metadata
- Placeholders are identifiable and timestamped for debugging
- Backend workflow can continue with description field (most important)
**Trade-off**: PDF filename may be generic if extraction fails

#### 8. **Polling Cleanup on Navigation**
**Decision**: Use Svelte `onDestroy` lifecycle hook to clear intervals
**Rationale**:
- Prevents memory leaks from abandoned polling
- Standard cleanup pattern in Svelte
- Ensures clean state management
**Trade-off**: Job continues processing server-side even if user navigates away

### Dependencies

**External Dependencies**:
- Node.js 20+ (LTS)
- npm or pnpm
- FastAPI backend running on localhost:8000 (dev)

**Package Dependencies** (see `package.json`):
- `@sveltejs/kit`: ^2.x
- `svelte`: ^5.x
- `typescript`: ^5.x
- `tailwindcss`: ^4.x
- `vite`: ^5.x
- `@sveltejs/adapter-static`: ^3.x

**Backend Prerequisites**:
- FastAPI server must be running
- Master CV must be loaded (`data/cv/master_cv.json`)
- LLM provider configured (OpenAI, DeepSeek, Anthropic, or Grok)

### Testing Strategy

#### Unit Tests (Vitest)
- **Input validation**: Test `validation.ts` functions
- **API client**: Mock fetch responses, test error handling
- **State management**: Test appState mutations

#### Component Tests (@testing-library/svelte)
- **JobDescriptionForm**: Test validation, submit behavior
- **ProgressStepper**: Test step transitions
- **ToastNotification**: Test auto-dismiss, close button

#### Integration Tests (Playwright)
- **Happy path**: Submit job → poll status → download PDF
- **Error handling**: Submit invalid input, backend failure
- **State reset**: Generate CV → reset → generate another

#### Manual Testing Checklist
- [ ] Submit valid job description, verify PDF downloads
- [ ] Submit empty textarea, verify validation error
- [ ] Submit while backend is down, verify error toast
- [ ] Test on mobile device (iOS Safari, Android Chrome)
- [ ] Test keyboard navigation (Tab, Enter, Escape)
- [ ] Test with slow network (throttle to 3G)

### Development Plan

#### Phase 1: Project Setup (1-2 hours)
1. **Use Context7 skill** to fetch latest Svelte 5 and SvelteKit documentation
2. Initialize SvelteKit project in `src/ui/`
3. Configure TypeScript, Tailwind v4, static adapter
4. Set up environment variables (`.env`, `VITE_API_BASE_URL`)
5. Create basic file structure

#### Phase 2: API Client & Types (1 hour)
1. Define TypeScript interfaces for API requests/responses
2. Implement `client.ts` with fetch wrappers
3. Add error handling and type validation

#### Phase 3: State Management (1 hour)
1. Create `appState.svelte.ts` with Svelte 5 runes
2. Implement state mutations (submit, poll, error, reset)
3. Add polling logic (setInterval with cleanup)

#### Phase 4: Components (3-4 hours)
1. **JobDescriptionForm.svelte**: Textarea, validation, submit button
2. **ProgressStepper.svelte**: Step indicators with animations
3. **ToastNotification.svelte**: Error/success notifications
4. **+page.svelte**: Main page layout and orchestration

#### Phase 5: Integration & Testing (2-3 hours)
1. Wire up components with appState
2. Test full workflow with running backend
3. Add error handling for edge cases
4. Polish animations and transitions

#### Phase 6: FastAPI Integration (1 hour)
1. Configure static file serving in `main.py`:
   - Serve UI from `/` (root path)
   - API uses `/api` prefix (already configured)
   - Mount SvelteKit build output (`src/ui/build/`)
2. Update CORS settings if needed
3. Test production build served by FastAPI
4. Document deployment process

#### Phase 7: Cleanup & Documentation (1 hour)
1. **Remove legacy endpoints** (`/api/cv/*`) from backend:
   - Delete `/api/cv/generate` endpoint
   - Delete `/api/cv/status/{job_id}` endpoint
   - Delete `/api/cv/download/{job_id}` endpoint
2. Verify no code references legacy endpoints (grep for `/api/cv/`)
3. Update README with UI setup instructions
4. Add screenshots to documentation

**Total Estimated Time**: 10-14 hours

## Out of Scope

### Explicitly NOT Included in MVP

1. **HITL Review UI**: No Tinder-like approval interface (Full mode)
2. **Job History**: No list of previous CV generations
3. **User Authentication**: No login/signup (single-user assumed)
4. **CV Preview**: No in-browser PDF preview (just download)
5. **Job URL Input**: No URL scraping (manual textarea only)
6. **Multiple CV Downloads**: No "download again" after initial download
7. **Advanced Validation**: No smart job description parsing on client
8. **Dark Mode**: Single theme only
9. **Internationalization**: English only
10. **Mobile App**: Web-only (responsive design)
11. **Offline Support**: Requires internet connection
12. **Real-time Collaboration**: Single user per session
13. **CV Customization**: No UI for tweaking generated CV
14. **Analytics/Telemetry**: No usage tracking

### Future Enhancements (Post-MVP)

1. **Full Mode UI**: Add HITL review page with Tinder-like swipe interface
2. **Job History**: LocalStorage-based recent jobs list
3. **URL Input**: Support pasting job posting URLs (lever.co, greenhouse.io)
4. **CV Preview**: Embed PDF viewer for in-browser preview
5. **Retry Workflow**: UI for providing feedback and regenerating CV
6. **Dark Mode**: Toggle for light/dark themes
7. **Export Options**: Download as DOCX, JSON, or Markdown
8. **Template Selection**: Choose CV template (modern, classic, minimal)
9. **Analytics**: Track usage patterns with privacy-friendly analytics

## Open Questions

### All Resolved ✅

1. ✅ **API Endpoint**: Use unified `/api/jobs/submit` (will remove legacy endpoints)
2. ✅ **Svelte Version**: Svelte 5 with Runes API
3. ✅ **Styling**: TailwindCSS v4
4. ✅ **Deployment**: Static build served by FastAPI
5. ✅ **Input Format**: Single textarea with LLM extraction
6. ✅ **Context7 Skill**: Use Context7 skill during implementation to fetch latest Svelte 5 documentation when needed
7. ✅ **PDF Auto-download Fallback**: Add manual "Download PDF" button if browser blocks auto-download
8. ✅ **Polling Cleanup**: Use `onDestroy` lifecycle hook to clear polling interval when user navigates away
9. ✅ **LLM Extraction Failure**: Use placeholder pattern `mvp-<date>-title`, `mvp-<date>-company` if LLM fails to extract
10. ✅ **FastAPI Static Serving**: Serve UI from `/` (root), API uses `/api` prefix (no conflicts)

## References

### Documentation
- [SvelteKit Documentation](https://kit.svelte.dev/docs)
- [Svelte 5 Runes API](https://svelte.dev/docs/svelte/what-are-runes)
- [TailwindCSS v4 Documentation](https://tailwindcss.com/docs)
- [FastAPI Static Files](https://fastapi.tiangolo.com/tutorial/static-files/)

### Related Files
- `src/api/main.py` - FastAPI backend with endpoints
- `src/models/unified.py` - Pydantic models for API types
- `src/agents/preparation_workflow.py` - CV generation workflow
- `CLAUDE.md` - Project architecture documentation
- `implementation-plan.md` - Source of truth for requirements

### Design Inspiration
- **Linear App**: Clean, minimal single-page input
- **Vercel Deploy UI**: Simple progress stepper with status updates
- **Stripe Dashboard**: Professional toast notifications
