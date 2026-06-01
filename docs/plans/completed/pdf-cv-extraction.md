# Feature Specification: PDF CV Upload & AI Extraction

## Overview
- **Feature**: PDF CV Upload & AI Extraction
- **Status**: Implemented (branch `feat/pdf-cv-extraction`, commit `55d3be9`)
- **Created**: 2026-05-21
- **Author**: User + Claude Code

## Problem Statement

The Master CV is currently stored as structured JSON conforming to `src/models/cv.py::CV`. Today, users can only populate it by:
1. Pasting JSON directly into the textarea in `CVUploadSection.svelte`, or
2. Uploading a `.json` file they hand-crafted from the template.

This is a hard adoption blocker for non-technical users — most candidates have their CV as a PDF, not a typed-out JSON document. The `Upload PDF CV` button already exists in `CVUploadSection.svelte` but is stubbed out via `WIPButton` + `WIP.PDF_CV_UPLOAD`. We need to wire it up so a user uploads their PDF résumé and the system uses an LLM to extract it into the structured CV JSON that the rest of the pipeline already consumes.

## Goals & Success Criteria

- A signed-in user can upload a PDF of their résumé in Settings → Master CV.
- The system extracts structured CV data via the user's configured LLM and populates the existing JSON editor with the result.
- The user reviews/edits the extracted JSON in-place and saves it through the existing `Save CV` flow — no new save path.
- Extraction quality is good enough that a typical 1-2 page tech CV produces JSON requiring no manual edits to pass `CV.model_validate()`.
- **Success Metrics**:
  - ≥ 90% of uploaded PDFs return a Pydantic-valid `CV` JSON on first attempt (measured via logging).
  - Median extraction latency < 30s for a 2-page PDF.
  - Zero new failure modes in the existing `Save CV` flow (the JSON editor remains the single save path).

## User Stories

1. As a new user setting up my profile, I want to upload my existing PDF résumé and have it converted to JSON automatically, so that I don't have to translate it field-by-field.
2. As a user updating my Master CV, I want to drop a newer PDF version in and have the JSON editor refresh with the extracted data, so that I can spot-check the changes before saving.
3. As a user whose LLM provider doesn't support PDF input, I want a clear message telling me how to fix it (switch model), so that I'm not stuck on an opaque error.
4. As a user, if extraction fails Pydantic validation, I want to see the raw extracted JSON plus the validation errors in the editor, so that I can fix the issues myself without re-uploading.

## Functional Requirements

### Core Capabilities

- **PDF Upload**: Accept a `.pdf` file selection from the existing Settings → Master CV section. Reject non-PDF, files > 10MB, or PDFs > 20 pages with a clear, inline error.
- **Async Extraction**: POST kicks off an in-memory background task; the UI polls a status endpoint until done.
- **Native PDF → LLM**: Send the raw PDF bytes to the LLM as a document content block (Anthropic `document`, OpenAI file input), instructing it to return JSON conforming to the `CV` schema.
- **Pydantic Validation**: Run `CV.model_validate(extracted_dict)` on the result. If it fails, still return the JSON to the UI, accompanied by a list of validation errors.
- **Preview in Existing Editor**: On success, populate the existing `cvText` textarea with the formatted JSON. User can edit, then click the existing `Save CV` button. No auto-save.
- **Single In-Flight Task per User**: Disable the upload button while extraction is running. No concurrent extractions per user.
- **Unsupported Provider Block**: If the user's configured CV-composition model is not in the PDF-capable allow-list, return 400 before kicking off the task with a message linking them to Model preferences.

### User Flows

**Happy path:**
1. User goes to Settings, opens the Master CV section.
2. User clicks `Upload PDF CV` (now enabled), picks `resume.pdf`.
3. Frontend POSTs `multipart/form-data` to `POST /api/users/me/master-cv/extract`.
4. Server validates MIME (cheap), checks provider capability (cheap), then atomically creates a task or returns 409 if one is already in flight. **Only then** does it read the request body and validate size + page count — so a misrouted upload doesn't load the full 10MB.
5. Server returns `202 { extraction_id, status: "pending" }` and creates an `asyncio.create_task` tracked on `AppContext._background_tasks`.
6. UI disables `Upload PDF CV` + `Save CV` (driven by the derived `extracting` flag) and polls `GET /api/users/me/master-cv/extract/{extraction_id}` every 2s. Browser state shows a transient `uploading` status before the POST returns, then transitions through `pending` → `running` → terminal.
7. On success (`status: "completed"`), UI fills the JSON textarea with `result_json` (pretty-printed) and re-enables the buttons. If `validation_errors` is non-empty, an inline amber panel lists them above the editor.
8. User edits if needed, clicks `Save CV` — existing flow persists to `UserTable.master_cv_json`.

**Validation-failure path (extraction succeeded but JSON didn't pass Pydantic):**
1. Same as above through step 7.
2. UI fills the JSON textarea AND shows the validation errors above the editor in an existing-style error box (one per validation error, e.g. `experiences[0].start_date: invalid date format`).
3. User fixes the issues in the editor, then clicks `Save CV`.

**Unsupported provider path:**
1. User clicks `Upload PDF CV` while on DeepSeek.
2. POST returns `400 { detail: "PDF extraction requires Anthropic Claude or OpenAI GPT-4. Update your CV composition model in Settings → Model preferences." }`.
3. UI surfaces this in the existing error box.

**Cancellation/staleness:**
1. If the user navigates away mid-extraction, the Svelte component's `onDestroy` clears the poll timer so no further `GET` calls fire. The background extraction task continues server-side and writes its result to the registry; on the user's next upload the previous record is fully evicted from `_by_id` (not just unlinked) so memory stays bounded by the number of distinct users.

### Data Model

**In-memory extraction-task registry** (no DB tables added):

```python
# src/services/cv/pdf_extraction.py
ExtractionStatus = Literal["pending", "running", "completed", "failed"]

@dataclass
class CVExtractionTask:
    id: str  # uuid4
    user_id: str
    status: ExtractionStatus = "pending"
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    result_json: dict | None = None       # populated on success
    validation_errors: list[str] = field(default_factory=list)  # populated on success even if invalid
    error_message: str | None = None      # populated on failure

class CVExtractionRegistry:
    """In-memory, per-process. One task per user; new uploads evict the previous record.

    All public methods are async and serialize via an internal `asyncio.Lock`.
    """
    _by_id: dict[str, CVExtractionTask]
    _by_user: dict[str, str]  # user_id -> current extraction_id
    _lock: asyncio.Lock

    async def create(self, user_id: str) -> CVExtractionTask: ...
    async def create_if_not_in_flight(self, user_id: str) -> CVExtractionTask | None:
        """Atomic check+create. Returns None if a pending/running task exists for the user."""
    async def get(self, extraction_id: str) -> CVExtractionTask | None: ...
    async def get_latest_for_user(self, user_id: str) -> CVExtractionTask | None: ...
    async def update(self, extraction_id: str, **fields) -> None: ...
```

The registry is held on `AppContext.cv_extraction_registry` and shared across requests.
`_CV_JSON_SCHEMA = CV.model_json_schema()` is module-level cached so the schema isn't recomputed per request.

**Response schemas** (`src/models/pdf_extraction.py`):

```python
class CVExtractionStartResponse(BaseModel):
    extraction_id: str
    status: Literal["pending"]

class CVExtractionStatusResponse(BaseModel):
    extraction_id: str
    status: Literal["pending", "running", "completed", "failed"]
    result_json: dict | None = None
    validation_errors: list[str] = Field(default_factory=list)
    error_message: str | None = None
```

### Integration Points

- **`src/llm/provider.py`**: `BaseLLMClient` gains a class attribute `SUPPORTS_PDF_INPUT: bool = False` and a default `generate_json_from_pdf(pdf_bytes, prompt, schema, *, temperature=0.1, max_tokens=8192)` that raises `NotImplementedError`. `AnthropicClient` (Messages API + `document` content block) and `OpenAIClient` (Responses API + `input_file` content block) override both. `LLMClientFactory.supports_pdf(provider: LLMProvider) -> bool` exposes the class-level flag without exposing the private `_clients` mapping.
- **`src/services/cv/pdf_extraction.py`** (new): `CVExtractionTask`, `CVExtractionRegistry`, `run_extraction(task, pdf_bytes, llm_client, registry)` background worker, and a `_format_validation_errors(ValidationError)` helper. Module-level `_CV_JSON_SCHEMA` cache.
- **`src/services/cv/cv_prompts.py`**: Module-level `CV_EXTRACTION_PROMPT` string (not loaded via the `PromptLoader` mechanism — the prompt has no template variables and the upload path doesn't need the file-based-prompt indirection).
- **`src/context.py`**: `cv_extraction_registry: CVExtractionRegistry | None` field on `AppContext`, instantiated in `create_app_context`.
- **`src/api/main.py`**: Two new endpoints (auth-required): `POST /api/users/me/master-cv/extract` and `GET /api/users/me/master-cv/extract/{extraction_id}`. Owner check on GET. Local helper `_resolve_cv_model_choice(user) -> (provider, model_or_None)` reuses the user's `cv_generation` model preference. Validation failures that occur **after** the task is created also mark the task `failed` so the user's in-flight guard clears.
- **`src/config/settings.py`**: `pdf_cv_upload_max_bytes: int = 10_485_760` (10 MB) and `pdf_cv_upload_max_pages: int = 20`. Defaults baked in; no `.env` change required.
- **`src/models/pdf_extraction.py`** (new): `CVExtractionStartResponse` and `CVExtractionStatusResponse`.
- **`ui/src/lib/components/settings/CVUploadSection.svelte`**: Replaces the `WIPButton` with a real button. Adds `handlePdfUpload`, `pollExtraction`, an `onDestroy` cleanup for the poll timer, a derived `extracting` flag (single source of truth, driven by `extractionStatus`), and an inline amber panel listing Pydantic validation errors.
- **`ui/src/lib/api/settings.ts`**: `extractCVFromPDF(file: File)`, `getCVExtractionStatus(id: string)`, and a private `extractDetail(response, fallback)` helper that surfaces FastAPI's `detail` field when present.
- **`ui/src/lib/wip/features.ts`**: `PDF_CV_UPLOAD` constant removed; `WIP.V1_BETA.tooltip` updated to drop the PDF CV upload mention.

## Technical Design

### Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  Browser (CVUploadSection.svelte)                              │
│                                                                │
│  [Upload PDF CV] ──► POST /api/users/me/master-cv/extract     │
│                       (multipart: pdf file)                    │
│         ▲                                                      │
│         │ poll every 2s                                        │
│         │                                                      │
│  GET /api/users/me/master-cv/extract/{id}                      │
│         ▲                                                      │
└─────────┼──────────────────────────────────────────────────────┘
          │
┌─────────┼──────────────────────────────────────────────────────┐
│  FastAPI (src/api/main.py)                                     │
│         │                                                      │
│  POST handler (cheap checks first to avoid wasted 10MB read):  │
│   1. Validate MIME (filename or content_type ends in pdf)      │
│   2. LLMClientFactory.supports_pdf(provider) — 400 if not      │
│   3. registry.create_if_not_in_flight(user_id) — 409 if exists │
│   4. await file.read() — validate size                         │
│   5. pypdf.PdfReader for page count — validate range           │
│   6. create_llm_client(provider, model_override)               │
│   7. ctx.create_background_task(run_extraction(task, bytes,    │
│      llm_client, registry))                                    │
│   8. Return 202 { extraction_id, status: "pending" }           │
│                                                                │
│  Any failure between steps 3 and 7 also updates the task to    │
│  status="failed" so the user's in-flight guard clears.         │
│                                                                │
│  GET handler:                                                  │
│   1. registry.get(extraction_id), 404 if missing               │
│   2. 403 if task.user_id != current_user.id                   │
│   3. Return status + result                                    │
└────────────────────────────────────────────────────────────────┘
          │
┌─────────▼──────────────────────────────────────────────────────┐
│  src/services/cv/pdf_extraction.py                             │
│                                                                │
│  run_extraction(task, pdf_bytes, llm_client):                  │
│    task.status = "running"                                     │
│    raw = llm_client.generate_json_from_pdf(pdf_bytes, prompt,  │
│                                            CVLLMOutput.schema) │
│    errors = validate_with_pydantic(raw)                        │
│    task.result_json = raw                                      │
│    task.validation_errors = errors                             │
│    task.status = "completed"                                   │
│  (on exception: task.status="failed", task.error_message=...)  │
└────────────────────────────────────────────────────────────────┘
```

### Technology Stack

- **Frameworks**: FastAPI (multipart upload), existing AppContext DI.
- **Libraries**:
  - `anthropic` (already a dep) — `messages.create` with `document` content block for PDF input.
  - `openai` (already a dep) — file inputs API for PDF if user is on GPT-4 vision-capable model.
  - `pypdf` (new dep) — used **only** for fast page-count validation server-side (not for text extraction). Lightweight; ~few hundred KB.
- **Tools**: existing async patterns.

### Data Persistence

- **None.** Extraction tasks live in a per-process in-memory registry. Lost on restart. This is acceptable because:
  - Extraction is short-lived (seconds, not minutes).
  - The end state is persisted via the existing `Save CV` flow (`UserTable.master_cv_json`).
  - Lost-on-restart is the same UX as a stale poll — user re-uploads.
- The PDF itself is never written to disk. It is held in memory only for the duration of the background task.

### API / Interface Design

**POST `/api/users/me/master-cv/extract`**
- Auth: required (existing `get_current_user`).
- Body: `multipart/form-data`, field `file` of type `application/pdf`.
- Returns `202 CVExtractionStartResponse`.
- Errors:
  - `400 {"detail": "File must be a PDF"}` — wrong MIME or extension.
  - `400 {"detail": "File exceeds 10MB limit"}` — too large.
  - `400 {"detail": "PDF exceeds 20-page limit"}` — too many pages.
  - `400 {"detail": "PDF extraction requires <list of supported providers>. Update Settings → Model preferences."}` — user's CV-composition model not in the allow-list.
  - `409 {"detail": "An extraction is already in progress"}` — current task for this user is `pending` or `running`. (Frontend prevents this but we enforce server-side too.)

**GET `/api/users/me/master-cv/extract/{extraction_id}`**
- Auth: required.
- Returns `200 CVExtractionStatusResponse`.
- Errors: `404` (no such id), `403` (not owner).

**LLM client interface additions** (`src/llm/provider.py`):
```python
class BaseLLMClient(ABC):
    SUPPORTS_PDF_INPUT: bool = False

    def generate_json_from_pdf(
        self,
        pdf_bytes: bytes,
        prompt: str,
        schema: dict | None = None,
        *,
        temperature: float = 0.1,
        max_tokens: int = 8192,
    ) -> dict:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support native PDF input"
        )


class LLMClientFactory:
    @classmethod
    def supports_pdf(cls, provider: LLMProvider) -> bool:
        """Return True if the provider's client supports native PDF input."""
        client_class = cls._clients.get(provider)
        return bool(client_class and client_class.SUPPORTS_PDF_INPUT)
```
`SUPPORTS_PDF_INPUT = True` on `AnthropicClient` and `OpenAIClient` only.
`LLMClientFactory.supports_pdf(provider)` is the public capability lookup the API uses — no reaching into the private `_clients` dict.

## Non-Functional Requirements

- **Performance**:
  - File-size/MIME/page-count checks complete in < 500ms (in-process, no LLM).
  - LLM extraction p95 < 60s for ≤ 20-page PDFs.
  - The HTTP POST itself returns immediately (< 1s) — actual LLM call is in a background task.
- **Security**:
  - Auth required for both endpoints; ownership check on status GET.
  - PDF bytes never written to disk; held in memory only during the task.
  - File-size and page-count limits prevent resource exhaustion / runaway LLM cost.
  - The user's existing LLM API key is used — no shared key escape.
- **Observability**:
  - `logger.info` on extraction start with `{user_id, file_size, page_count, model}`.
  - `logger.info` on completion with `{user_id, duration_ms, validation_errors_count}`.
  - `logger.warning` on Pydantic validation failure with the error summary.
  - `logger.exception` on any unhandled exception inside `run_extraction`.
- **Error Handling**:
  - LLM rate-limit / 5xx: caught, task marked `failed`, `error_message` set to a user-friendly string.
  - JSON parse error from LLM: caught, retried once with a short reminder prompt; second failure → `failed`.
  - Validation error: NOT a task failure — completed with errors surfaced in `validation_errors`.

## Implementation Considerations

### Design Trade-offs

- **In-memory task registry vs DB-backed**: chose in-memory because extraction is short-lived, no need to survive restarts (worst case: user re-uploads), and avoids a new table + migration. Trade-off accepted: load-balanced multi-instance deploys won't work without sticky sessions — fine for current single-process deployment.
- **Block unsupported providers vs fall back to text extraction**: chose to block. Falling back to `pypdf` text extraction silently degrades quality (loses layout/columns/tables), which would surprise users who picked DeepSeek for cost. A clear error pushes them to make an informed choice.
- **Preview in editor vs auto-save**: chose preview. The LLM can hallucinate (especially names/dates), and the existing JSON editor + `Save CV` button already give the user a review-before-commit checkpoint. Adding auto-save would skip this gate and confuse the existing mental model.
- **Async + polling vs synchronous request**: synchronous would have been simpler, but the user explicitly chose async. The polling overhead is minimal (2s interval, lightweight JSON status).
- **Pydantic-validate but don't block vs strict retry**: chose validate-but-don't-block. LLMs sometimes return things like `start_date: "Sep 2020"` instead of `"2020-09-01"`. A retry would burn tokens; surfacing the error and letting the user fix it in the textarea is faster.
- **Add `pypdf` only for page-count check**: we deliberately don't use `pypdf` for text extraction even though the dep is available. Native LLM PDF input preserves layout fidelity, which is the whole point of going through this path.

### Dependencies

- **New Python deps**:
  - `pypdf>=4.0.0` — used for page-count validation only.
  - `python-multipart>=0.0.9` — required by FastAPI to parse `multipart/form-data`; not previously a project dep.
- **Existing**: `anthropic` (already pinned ≥ 0.18.0), `openai` (already pinned ≥ 1.10.0).
- **Provider capabilities**:
  - **Anthropic**: PDF document input via the Messages API (`content: [{type: "document", source: {type: "base64", media_type: "application/pdf", data: "..."}}, ...]`).
  - **OpenAI**: PDF input via the Responses API with `input_file` content block (data URL with base64 payload), GPT-4 family.
  - **DeepSeek / Grok**: No native PDF input — blocked with clear error at the API layer.

### Testing Strategy

- **Unit (`tests/unit/test_pdf_extraction.py`, 20 tests, all passing)**:
  - Provider capability flags: `AnthropicClient.SUPPORTS_PDF_INPUT == True`, `OpenAIClient.SUPPORTS_PDF_INPUT == True`, `DeepSeekClient` and `GrokClient` report `False`. `BaseLLMClient` default raises `NotImplementedError`.
  - `CVExtractionRegistry.create/get/update` behavior, **eviction**-on-new-upload (the previous task for a user is removed from `_by_id`), and `create_if_not_in_flight` returning `None` while a task is running but creating fresh when the prior is terminal.
  - `run_extraction` happy path with a mocked LLM client returning valid JSON.
  - `run_extraction` with mocked LLM returning JSON that fails Pydantic — task `completed`, `validation_errors` populated, `result_json` still present.
  - `run_extraction` with mocked LLM raising — task `failed`, `error_message` set.
  - `run_extraction` with `NotImplementedError` from the client — task `failed` with a "does not support" message.
  - `run_extraction` with non-dict JSON response — task `failed`.
  - `run_extraction` JSON-decode-error path: first call raises, second call succeeds (single retry honored).
- **API (`tests/unit/test_pdf_extraction_api.py`, 11 tests, all passing)** — co-located with the unit tests since the repo has no `tests/integration/` directory:
  - POST with non-PDF MIME → 400.
  - POST with >10MB file → 400.
  - POST when user's model doesn't support PDF → 400 with provider message.
  - POST with >20-page PDF (via patched `pypdf.PdfReader`) → 400.
  - POST happy path → 202 + extraction_id.
  - POST while another task is `running` → 409.
  - POST with corrupt PDF (`PdfReadError` raised by patched reader) → 400.
  - GET status returns owner's task with `result_json` + `validation_errors`.
  - GET 404 when extraction_id is unknown.
  - GET 403 when extraction_id belongs to a different user.
  - Route-registration sanity check.
- **E2E (`tests/e2e/`)**: **Deferred.** Not added in the initial implementation — Playwright fixture work plus a redacted sample PDF were judged out of scope for the first cut. Tracked as follow-up.
- **Eval (optional, deferred)**: Add to `tests/eval/` later — score extraction quality against a small set of real PDFs.

## Out of Scope

- Storing the original PDF file anywhere (filesystem or DB).
- Re-extracting from a previously uploaded PDF (we don't keep it).
- DOCX, RTF, or other résumé formats — PDF only.
- Multi-PDF merge / multi-file batch upload.
- Background queue / multi-instance coordination (in-memory only).
- Auto-merging extracted JSON with an existing CV — full overwrite of the editor textarea, user decides what to save.
- Adding `pdf_extraction` as a new operation in `model_preferences` — we reuse `cv_composition` so users have one fewer dropdown to configure.
- Streaming partial extraction results to the UI.

## Open Questions

- ~~**OpenAI document input availability**: confirm at implementation time which OpenAI models in the user's model catalog (`/api/llm/models`) support PDF input via Responses API.~~ **Resolved**: implemented against OpenAI Responses API with `input_file` content block on the GPT-4 family; gating is by `SUPPORTS_PDF_INPUT` class flag on `OpenAIClient`.
- **Sample PDF for E2E**: still open — E2E test was deferred.
- ~~**Validation error surfacing format**~~: **Resolved** — full list, amber-bordered panel above the editor, one bullet per `loc: msg` line.

## As-Built Notes (divergences from the original draft)

These were resolved during implementation in commit `55d3be9` after a high-effort
three-agent code review (reuse / quality / efficiency). Listed here so future
readers can tell intent from accident.

1. **Validation order reversed.** The original plan validated MIME + size +
   page-count first, then checked provider capability. The code reverses that:
   cheap checks (MIME, provider capability, in-flight) run **before** reading the
   10MB body. Reason: a DeepSeek user or a double-submit shouldn't pay the
   bandwidth + memory cost of a rejected request.
2. **Atomic in-flight guard via `create_if_not_in_flight`.** The plan described
   "check `get_latest_for_user`, then `create`" — a TOCTOU race for double-clicks.
   The registry now exposes an atomic check+create under its lock.
3. **Eviction-on-create.** Plan said the previous task "remains reachable by ID
   until the next upload overwrites the pointer." The code now fully removes the
   previous task from `_by_id` so memory is bounded by the number of distinct
   users (not total uploads). The "still reachable by ID" semantics gained
   nothing — the user couldn't retrieve the old ID anyway.
4. **`LLMClientFactory.supports_pdf` instead of reaching into `_clients`.** Added
   a public classmethod so the API doesn't poke the private mapping.
5. **`SUPPORTS_PDF_INPUT` is a class attribute, not a module-level constant.**
   Discoverable from any client subclass; matches the rest of the provider
   surface (which uses class attributes for model lists).
6. **Validation failures after `create_if_not_in_flight` mark the task `failed`.**
   The plan returned HTTP 400 for size/page/etc. but never said what should
   happen to the freshly-created task row. The endpoint now writes
   `status="failed"` so the user's in-flight guard clears on the next attempt
   without waiting for any timeout.
7. **`CV.model_json_schema()` cached at module load.** Avoids re-traversing the
   Pydantic schema on every extraction.
8. **`python-multipart` is a new runtime dependency.** Required by FastAPI to
   parse the `multipart/form-data` upload — the plan implied FastAPI 0.109+
   already had it, but the project's pinned setup did not.
9. **UI: derived `extracting` flag, `onDestroy` poll cleanup.** Plan didn't
   prescribe cleanup; the implementation adds `onDestroy(() => clearTimeout(...))`
   so navigating away mid-extraction doesn't leak the timer.
10. **Tests live under `tests/unit/`**, not `tests/integration/`. The repo has
    no integration directory yet and the API tests use `TestClient` with mocked
    LLM and a stubbed `src.agents._shared` module (which would otherwise pull in
    WeasyPrint native libs).
11. **E2E test deferred.** Not included in the initial implementation.

## References

- Stubbed UI (replaced): `ui/src/lib/components/settings/CVUploadSection.svelte`
- WIP flag (removed): `ui/src/lib/wip/features.ts` (`WIP.PDF_CV_UPLOAD`)
- CV schema: `src/models/cv.py::CV`, `src/models/cv.py::CVLLMOutput`
- Existing CV save endpoint: `src/api/main.py::update_user_profile` (PUT `/api/users/me`)
- LLM provider base: `src/llm/provider.py::BaseLLMClient`
- Factory capability lookup: `src/llm/provider.py::LLMClientFactory.supports_pdf`
- Registry + worker: `src/services/cv/pdf_extraction.py`
- API response models: `src/models/pdf_extraction.py`
- AppContext: `src/context.py::AppContext`
- Project conventions: `CLAUDE.md` (async-native workflows, AppContext DI, repository pattern)
