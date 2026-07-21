# Easy Apply — Happy Path (No-LLM, Server-Orchestrated Bridge)

## Overview

Implement the happy path of LinkedIn **Easy Apply** automation per `docs/plans/ARCHITECTURE-browser-agent.md`, **without any LLM agent** (deferred to a later sprint). A Chrome MV3 extension acts as a dumb DOM actuator in the user's real logged-in browser; the existing FastAPI/LangGraph backend orchestrates the multi-step form fill **deterministically** over a WebSocket bridge, driving per-field tools (`read_form_state` → `fill_field` → `advance_step` → `submit_form`). Field classification, recovery, and DOM primitives are **ported from the proven** `AutoApplyMax` extension (`/Users/kelizarov/Repos/experiments/browser-extensions/AutoApplyMax/content-simple.js`) rather than reinvented.

Triggering: HITL **approve** dispatches an apply; a new `user.auto_apply` flag lets scraped+filtered jobs apply directly (skip HITL). Any field that can't be filled from known profile/CV data → **abort and mark `manual_required`** (never guess). If no extension is connected when an apply fires → **fail fast** to a recoverable `needs_extension` state.

## Context

- **Files involved:**
  - `docs/plans/ARCHITECTURE-browser-agent.md` (existing — the target architecture; §4 states, §5 tools, §8 auth, §9 auto-apply, §11 build order)
  - `/Users/kelizarov/Repos/experiments/browser-extensions/AutoApplyMax/content-simple.js` (existing reference — **the cookbook**; cite line numbers below, do NOT import)
  - `src/models/user.py` (existing — `User`, `UserUpdateRequest`, `UserSearchPreferences`; add `ApplyProfile`, `apply_profile`, `auto_apply`)
  - `src/models/state_machine.py` (existing — `BusinessState`, `ALLOWED_TRANSITIONS`; add `MANUAL_REQUIRED`, `NEEDS_EXTENSION`)
  - `src/services/db/tables.py` (existing — `UserTable` with `role`/`filter_preferences` runtime-migrated columns; add `apply_profile`, `auto_apply`)
  - `src/services/auth/user_repository.py` (existing — `initialize()` runtime-migration pattern, `update()` field mapping, `_row_to_user`; extend for new fields)
  - `src/services/jobs/hitl_processor.py` (existing — `_handle_approve` at :207 currently a no-op stub; wire to apply dispatch)
  - `src/agents/dispatcher.py` (existing — `WorkflowDispatcher.dispatch_preparation`/`dispatch_retry`; add `dispatch_application`)
  - `src/agents/preparation_workflow.py` (existing — `save_to_db_node` at :621 sets PENDING/COMPLETED; add `auto_apply` → APPROVED branch)
  - `src/api/main.py` (existing — REST endpoints, AppContext wiring; mount WS endpoint, add apply-retry route, extend `PUT /api/users/me`)
  - `src/context.py` (existing — `AppContext` DI; add `session_store`, `bridge_client`, `apply_workflow`)
  - `src/config/settings.py` (existing — `Settings`; add apply timeouts + feature flag)
  - `ui/src/routes/settings/` + auth store (existing — add Application Profile card + `auto_apply` toggle; new `/extension-auth` route)
  - **New:** `extension/` (MV3: `manifest.json`, `background.js`, `content_script.js`, `popup/`)
  - **New:** `src/bridge/ws_relay.py`, `src/bridge/session_store.py`
  - **New:** `src/services/linkedin/easy_apply_selectors.py`, `src/services/linkedin/field_classifier.py`, `src/services/linkedin/apply_bridge.py`
  - **New:** `src/agents/application_workflow.py` (was deleted in `9ced769`; re-create deterministic version)
- **Related patterns:** LangGraph async-native nodes receiving deps via `config["configurable"]`; repository runtime-migration (mirror `role`/`filter_preferences`); `WorkflowDispatcher` for tracking + failure recovery; thin API adapters → domain services; Pydantic v2 models; per-user data ownership (`get_for_user`).
- **Dependencies:** `playwright_stealth` already present (unused here). FastAPI WebSocket support is built-in (no new dep). No MCP SDK / `claude-agent-sdk` this sprint. Chrome extension is vanilla JS (no build step).

## Development Approach

- **Testing approach: Regular** (code first, then tests), except `field_classifier.py` which is **TDD** (its correctness is the core risk — write fixture-driven tests first).
- The bridge "tools" are **plain async Python methods** on `ApplyBridge` (NOT `create_sdk_mcp_server` / `@tool`). Write their signatures so the future LLM sprint can wrap them with MCP **without changing the WS protocol**. (YAGNI: no MCP layer until there's an agent to consume it.)
- **PII / placeholder substitution is OUT this sprint** — with no LLM there is no untrusted context to protect; the server already holds the real values and sends them straight to `fill_field`. Note this deferral in code comments where the LLM sprint will add it.
- Port AutoApplyMax **logic and selectors**, not files. DOM primitives live in the content script; the label→field **classification regexes move server-side into Python**.
- Use the async API consistently; never block the event loop. Bridge RPCs must have timeouts and correlation IDs.
- **CRITICAL: every task MUST include new/updated tests.**
- **CRITICAL: all tests must pass before starting the next task.**

## Implementation Steps

### Task 1: Data model, states & persistence for ApplyProfile + auto_apply

**Files:**
- Modify: `src/models/user.py`
- Modify: `src/models/state_machine.py`
- Modify: `src/services/db/tables.py`
- Modify: `src/services/auth/user_repository.py`

- [ ] In `src/models/user.py` add `class ApplyProfile(BaseModel)` with fields: `phone_country_code: str | None`, `years_experience: int | None`, `expected_salary: str | None`, `needs_visa_sponsorship: bool | None`, `legally_authorized: bool | None`, `willing_to_relocate: bool | None`, `drivers_license: bool | None` (all optional; absence = "unknown" → abort path).
- [ ] Add `apply_profile: ApplyProfile | None = None` and `auto_apply: bool = False` to `User`; add the same two optional fields to `UserUpdateRequest`.
- [ ] Add `ApplyProfile.is_complete_for(required_kinds: set[str]) -> bool` helper used by the classifier/abort logic.
- [ ] In `src/models/state_machine.py` add `BusinessState.MANUAL_REQUIRED = "manual_required"` (terminal) and `BusinessState.NEEDS_EXTENSION = "needs_extension"` (recoverable).
- [ ] Update `ALLOWED_TRANSITIONS`: `APPROVED` → add `NEEDS_EXTENSION`, `MANUAL_REQUIRED`; `APPLYING` → add `MANUAL_REQUIRED`; add `NEEDS_EXTENSION: {APPLYING, FAILED}`; add `MANUAL_REQUIRED: set()`; add `APPROVED` to the target sets of `QUEUED` and `PROCESSING` (for the `auto_apply` save path).
- [ ] In `src/services/db/tables.py` add `apply_profile = JSON(null=True)` and `auto_apply = Boolean(default=False)` to `UserTable`.
- [ ] In `user_repository.py` `initialize()`: runtime-migrate both columns (mirror the `role`/`filter_preferences` `ALTER TABLE ... ADD COLUMN` guards); in `update()` map `apply_profile` (`.model_dump()`) and `auto_apply`; in `_row_to_user` parse them back (`_parse_json_field`).
- [ ] Write unit tests: `ApplyProfile` round-trip, `is_complete_for`, new transition validity (`APPROVED→NEEDS_EXTENSION`, `NEEDS_EXTENSION→APPLYING`, illegal `MANUAL_REQUIRED→APPLYING` raises), repository save/load of `apply_profile`+`auto_apply` incl. the runtime migration on a column-less DB.
- [ ] Run project test suite - must pass before task 2.

### Task 2: WebSocket bridge — relay + session store

**Files:**
- Create: `src/bridge/__init__.py`
- Create: `src/bridge/session_store.py`
- Create: `src/bridge/ws_relay.py`
- Modify: `src/config/settings.py`
- Modify: `src/context.py`

- [ ] `settings.py`: add `easy_apply_enabled: bool = True`, `apply_per_app_timeout_seconds: int = 180`, `apply_stuck_timeout_seconds: int = 120`, `apply_rpc_timeout_seconds: int = 30`, `apply_daily_limit_detection: bool = True`.
- [ ] `session_store.py`: `class SessionStore` — in-memory registry mapping `user_id → WebSocket` (one active session per user; newest wins). Methods: `register(user_id, ws)`, `unregister(user_id)`, `is_connected(user_id) -> bool`, `get(user_id) -> WebSocket | None`. Guard with `asyncio.Lock`.
- [ ] `ws_relay.py`: `class WsRelay` wrapping a `SessionStore`. `async def handle_connection(ws)`: accept, require first frame `{"type":"auth","token":...}`, validate via `auth_service` JWT decode (reuse existing decode used by `get_current_user`); on success bind `user_id`, send `{"type":"ready"}`, register session; then loop receiving frames and resolving pending RPC futures by `id`. `async def send_rpc(user_id, method, params, timeout) -> dict`: assign correlation `id`, send `{"type":"rpc","id","method","params"}`, await a `Future` resolved by the receive loop, raise `BridgeTimeout`/`BridgeDisconnected` on failure. Unregister on disconnect and fail all pending futures.
- [ ] `context.py`: construct `SessionStore` + `WsRelay` in `create_app_context()`, expose as `ctx.session_store` / `ctx.ws_relay`.
- [ ] Write unit tests with a fake WebSocket: auth accept/reject (bad JWT), `is_connected` lifecycle, `send_rpc` correlation + result resolution, timeout, and disconnect failing pending futures.
- [ ] Run project test suite - must pass before task 3.

### Task 3: Chrome MV3 extension (actuator) + extension-auth route

**Files:**
- Create: `extension/manifest.json`
- Create: `extension/background.js`
- Create: `extension/content_script.js`
- Create: `extension/popup/popup.html`, `extension/popup/popup.js`, `extension/popup/popup.css`
- Create: `ui/src/routes/extension-auth/+page.svelte`

- [ ] `manifest.json`: MV3; `permissions: ["tabs","storage","scripting"]`; `host_permissions: ["https://www.linkedin.com/*"]`; background `service_worker: background.js`; `externally_connectable` for the app origin so `/extension-auth` can post the JWT. **No declarative `content_scripts`** — inject on demand (AutoApplyMax §9 security model).
- [ ] `extension/extension-auth` flow: `+page.svelte` reads the app JWT and calls `chrome.runtime.sendMessage(EXT_ID, {type:"SET_TOKEN", token})`; `background.js` stores it in `chrome.storage.session` (clears on browser close, per ARCHITECTURE §8).
- [ ] `background.js`: open WebSocket to the configured `wss://<app>/ws/extension`, send `{"type":"auth","token}"`; on `{"type":"rpc",...}` route the method to the active LinkedIn tab via `chrome.tabs.sendMessage` (inject `content_script.js` via `chrome.scripting.executeScript` if not present), await the content-script reply, send `{"type":"result","id","result"}` back. Auto-reconnect with backoff; relay status to popup.
- [ ] `content_script.js`: implement the **DOM primitives** invoked by the server tools, gated by `isRunning`/`userExplicitlyConnected` flags + gated `click()`/`fill()` (port AutoApplyMax §9, `:33-46`, `:417-429`):
  - `serialize_form()` → `{step, total, fields:[{selector,label,type,options,required}], flags:{has_spinner,modal_present,page_text_excerpt}}` (label assembled from `aria-label`+`name`+`<label for>`+parent, per AutoApplyMax `:872-918`).
  - `fill_field(selector, value)` — text/email/tel/number + custom listbox open/select (port `:872-979`, `:1282-1358`), native `<select>` (`:1213-1280`), checkbox (`:1111-1128`), radio (`:1130-1211`).
  - `upload_file(selector, dataUrl, filename, mime)` via `DataTransfer` (port `base64ToFile`/`fillFileInput` `:432-473`).
  - `click_button(role)` for Next/Review/Submit (finder `:1363-1367`); `find_and_click_done()` (port the 4-method × 3-strategy finder `:126-281`).
  - `discard_application()` (port `:298-414`); `reload_page()`; `take_screenshot()` (popup/page capture for confirmation).
- [ ] `popup/`: minimal UI — connection status, "Connect" (opens `/extension-auth`), Pause/Resume, last-apply result. No profile form (profile lives in app Settings).
- [ ] Add `EXTENSION_ID` / app origin to `settings.py` + `.env.example` as needed for `externally_connectable`.
- [ ] Write tests: `manifest.json` is valid JSON with the required keys/permissions and no `content_scripts` block; a Node/JSDOM (or pytest-driven `node`) unit test of `serialize_form()` + `fill_field()` against a saved Easy Apply modal HTML fixture (capture one into `tests/fixtures/easy_apply_modal.html`).
- [ ] Run project test suite - must pass before task 4.

### Task 4: Field classifier + Easy Apply selectors (TDD)

**Files:**
- Create: `src/services/linkedin/easy_apply_selectors.py`
- Create: `src/services/linkedin/field_classifier.py`
- Create: `tests/services/linkedin/test_field_classifier.py`

- [ ] **(TDD)** First write `test_field_classifier.py` with serialized-field fixtures covering: first/last name, email, phone, city/location, years-of-experience, salary, visa sponsorship, work authorization, relocation, driver's license, language-proficiency dropdown, consent checkbox, and an **unrecognized** screening question.
- [ ] `easy_apply_selectors.py`: `EASY_APPLY_SELECTORS` dict ported from AutoApplyMax — modal (`.jobs-easy-apply-modal`), job card (`li[data-occludable-job-id]`), easy-apply button (`button.jobs-apply-button`), Next/Review/Submit, `#follow-company-checkbox`, error/`[role=alert]`, spinner; plus `DAILY_LIMIT_PATTERNS` (the ~11 messages, `:60-123`) and `DONE_TEXTS`/`DONE_CONTROL_NAMES` (`:126-281`). Mirror the dual-layout commenting style of `selectors.py`.
- [ ] `field_classifier.py`: `classify_field(field, apply_profile, contact_info) -> FieldFill | Unknown`. Port AutoApplyMax's **multilingual label regexes** (EN/FR/ES/DE/IT) for text/radio/select kinds (`:872-979`, `:1130-1358`); resolve the value from `ApplyProfile`/CV `ContactInfo`; consent checkboxes → check (`:1111-1128`); language proficiency → Native>Fluent>Professional (`:1213-1280`). Return `Unknown(reason, label)` for anything unmatched **or** matched-but-profile-value-missing → caller aborts to `manual_required`. **No "answer Yes / pick option 1" fallback.**
- [ ] Make the TDD tests pass; add an explicit test asserting unknown/missing-value fields return `Unknown` (never a guess).
- [ ] Run project test suite - must pass before task 5.

### Task 5: Apply bridge client (deterministic tools)

**Files:**
- Create: `src/services/linkedin/apply_bridge.py`
- Modify: `src/context.py`

- [ ] `apply_bridge.py`: `class ApplyBridge` constructed with `WsRelay` + settings. Async methods (the future MCP tool surface):
  - `read_form_state(user_id) -> FormState` — RPC `serialize_form`; run `field_classifier` over each field; attach per-field `fill_plan` and a list of `unknown_fields`; detect daily-limit from `flags.page_text_excerpt` against `DAILY_LIMIT_PATTERNS`.
  - `fill_field(user_id, selector, value)` — RPC; raise on `error`.
  - `advance_step(user_id) -> {advanced, errors[]}` — RPC `click_button("next"|"review")`, wait, re-`serialize_form`, scan `[role=alert]`/inline errors (`:800-825`, `:1436-1477`).
  - `upload_file(user_id, selector, pdf_path)` — read the tailored PDF from `data/generated_cvs/{user_id}/{job_id}.pdf`, base64-encode, RPC `upload_file`.
  - `submit_form(user_id) -> {confirmed, screenshot_b64}` — RPC un-follow company (`:1377-1408`), `click_button("submit")`, `find_and_click_done`, capture confirmation (`:1479-1545`).
  - `discard(user_id, reason)` — RPC `discard_application`.
- [ ] All methods honor `apply_rpc_timeout_seconds`; translate `BridgeDisconnected` into a typed error the workflow maps to `needs_extension`.
- [ ] Expose `ctx.apply_bridge` in `create_app_context()`.
- [ ] Write unit tests with a mock `WsRelay`: `read_form_state` classification + unknown-field surfacing + daily-limit flag; `submit_form` un-follow-then-submit ordering; `upload_file` reads+encodes the right path; disconnect → typed error.
- [ ] Run project test suite - must pass before task 6.

### Task 6: Application workflow (deterministic LangGraph) + dispatcher

**Files:**
- Create: `src/agents/application_workflow.py`
- Modify: `src/agents/dispatcher.py`
- Modify: `src/context.py`

- [ ] `application_workflow.py`: build a LangGraph mirroring ARCHITECTURE §4 (deterministic, no LLM). State `ApplyWorkflowState` (job_id, user_id, job_url, pdf_path, apply_profile, contact_info). Nodes/edges:
  - `open_easy_apply` → RPC navigate + click Easy Apply (handle safety-reminder modal `:665-687`); verify modal opened.
  - `fill_step` (loop, max 10 steps `:778`): `read_form_state` → if `unknown_fields` non-empty → `discard` + `manual_required`; else `fill_field` for each `fill_plan`; `advance_step`; on validation `errors` it can't fix → `discard` + `manual_required`; on Submit-present → `submit`.
  - `submit` → `submit_form`; `confirmed` → `applied`; else `failed`.
  - Cross-cutting: per-app wall-clock timeout (`apply_per_app_timeout_seconds`) → discard+fail; daily-limit flag → stop + record (do not retry); `BridgeDisconnected` → `needs_extension`.
  - Terminal writes: `APPLIED` (+ `application_url`, store confirmation screenshot path), `MANUAL_REQUIRED` (+ reason), `NEEDS_EXTENSION`, `FAILED` (+ error). Persist via repository, respecting `ALLOWED_TRANSITIONS`.
- [ ] `dispatcher.py`: add `dispatch_application(*, job_id, thread_id, initial_state, user_id)` mirroring `dispatch_retry` (track on AppContext, FAILED on exception respecting transitions).
- [ ] `context.py`: compile the apply workflow once, expose `ctx.apply_workflow`; wire `dispatch_application` into `WorkflowDispatcher`.
- [ ] Write unit tests with a stubbed `ApplyBridge`: happy path 3-step form → `APPLIED`; unknown field → `MANUAL_REQUIRED` + discard called; daily-limit → stops without submit; disconnect → `NEEDS_EXTENSION`; per-app timeout → `FAILED`.
- [ ] Run project test suite - must pass before task 7.

### Task 7: Triggers — HITL approve, auto_apply branch, API surface

**Files:**
- Modify: `src/services/jobs/hitl_processor.py`
- Modify: `src/agents/preparation_workflow.py`
- Modify: `src/api/main.py`

- [ ] Add a shared `trigger_apply(ctx, job_id, user_id)` helper (in `hitl_processor.py` or a small module): if `ctx.session_store.is_connected(user_id)` → set `APPLYING` + `dispatch_application`; else set `NEEDS_EXTENSION` with message "Open the extension in your browser to apply."
- [ ] `hitl_processor.py` `_handle_approve` (:207): replace the no-op — set `APPROVED` then call `trigger_apply`. Return a response reflecting `APPLYING` vs `NEEDS_EXTENSION`.
- [ ] `preparation_workflow.py` `save_to_db_node` (:621): in full mode, if `user.auto_apply` is True → save status `APPROVED` and call `trigger_apply` (else `PENDING` as today). Load `user.auto_apply` via `user_repository` (already available in config).
- [ ] `api/main.py`: mount `WS /ws/extension` delegating to `ctx.ws_relay.handle_connection`; add `POST /api/jobs/{job_id}/apply` (user-scoped) that re-runs `trigger_apply` for a job in `NEEDS_EXTENSION`/`APPROVED` (manual retry once the extension connects); ensure `PUT /api/users/me` persists `apply_profile` + `auto_apply` (extend the existing `UserUpdateRequest` handling).
- [ ] Write tests (TestClient + mock bridge/session): approve with connected session → `APPLYING` + dispatch invoked; approve with no session → `NEEDS_EXTENSION`; `auto_apply=True` prep save → `APPROVED` + trigger; `POST /api/jobs/{id}/apply` re-dispatches; WS endpoint rejects a missing/invalid JWT.
- [ ] Run project test suite - must pass before task 8.

### Task 8: Frontend — Application Profile, auto_apply toggle, status badges

**Files:**
- Modify: `ui/src/routes/settings/+page.svelte` (and any settings store/api client)
- Modify: HITL review + history components (status badge map)
- Modify: auth store if it surfaces user fields

- [ ] Settings: add an "Application Profile" card editing all `ApplyProfile` fields + an `auto_apply` toggle; save via `PUT /api/users/me`. Show a hint that an incomplete profile causes applies to abort to "manual required".
- [ ] Add badges/labels for the new statuses `applied`, `manual_required` (with a "Finish manually" link to the LinkedIn job URL), and `needs_extension` (with an "Apply now" button hitting `POST /api/jobs/{id}/apply`).
- [ ] Surface extension connection state where useful (e.g., a banner prompting "Connect extension" linking to `/extension-auth`).
- [ ] Write/extend frontend tests (component or existing E2E harness) for the Application Profile form save and the new status rendering.
- [ ] Run project test suite - must pass before task 9.

### Task 9: Verify acceptance criteria

- [ ] Manual test (happy path): with the extension connected and a complete Application Profile, approve a real LinkedIn Easy Apply job (≤3 steps, no novel screening questions) → status reaches `applied`, confirmation screenshot captured, application visible in LinkedIn.
- [ ] Manual test (abort): approve a job whose form has an unrecognized required question → status `manual_required`, modal discarded cleanly.
- [ ] Manual test (no extension): approve with the extension disconnected → status `needs_extension`; connect + "Apply now" → applies.
- [ ] Manual test (auto_apply): enable `auto_apply`, run a LinkedIn search → a filtered-in job applies without HITL.
- [ ] Run full test suite: `pytest`
- [ ] Run linter/format/type: `ruff check src/ && black --check src/ && mypy src/`
- [ ] Verify test coverage ≥80% for new `src/bridge/`, `src/services/linkedin/field_classifier.py`, `apply_bridge.py`, and `application_workflow.py`.

### Task 10: Update documentation

- [ ] Update `CLAUDE.md`: mark Application Workflow ✅ (deterministic, no-LLM); document the WS bridge, `ApplyProfile`/`auto_apply`, new `BusinessState`s (`manual_required`, `needs_extension`), the `extension/` directory, and the new endpoints (`WS /ws/extension`, `POST /api/jobs/{id}/apply`).
- [ ] Amend `docs/plans/ARCHITECTURE-browser-agent.md` to record the no-LLM deltas actually built: recovery states + `discard` tool, deterministic classifier in place of the agent, MCP/placeholder/vision **deferred** to the LLM sprint (and note the bridge tool signatures are MCP-wrap-ready).
- [ ] Update `README.md` if user-facing (extension install + connect steps).
- [ ] Move this plan to `docs/plans/completed/`.

## Out of Scope (deferred to the LLM sprint)

- LLM form-fill agent (Claude Agent SDK) and per-field LLM decisions — the bridge tools are written MCP-wrap-ready so this is additive.
- Vision fallback subagent / `take_screenshot`-driven field extraction for non-LinkedIn ATS (the `take_screenshot` primitive is built but only used for confirmation capture now).
- Placeholder/PII substitution in tool results + Langfuse traces (no LLM context to protect yet).
- AutoApplyMax-style "guess" answers for unknown questions (deliberately excluded — we abort to `manual_required`).
- Post-submission email/Telegram notification for `auto_apply` (nice-to-have; can follow).

## Key Design Decisions (why)

- **Server-orchestrated deterministic bridge, not content-script-driven:** keeps the architecture's per-field tool boundary so the LLM sprint is a drop-in, while staying multi-user and server-authoritative. Trade-off accepted: more plumbing now, per-field RPC latency (mitigated — only a handful of fields per form).
- **Abort → `manual_required` on unknowns:** correctness over throughput; the product's value is tailored, correct applications, so we never guess screening answers.
- **`ApplyProfile` server-side in Settings:** single source of truth alongside master CV / search prefs; captured once, reused.
- **Fail-fast `needs_extension` (recoverable):** no server-side apply queue to manage; user re-triggers via `POST /api/jobs/{id}/apply` once connected.
- **Port AutoApplyMax logic, not files:** DOM primitives → content script; label classification regexes → Python; resilience (discard, Done-finder, daily-limit, per-app timeout, un-follow) ported wholesale as the hardest-won, proven parts.
