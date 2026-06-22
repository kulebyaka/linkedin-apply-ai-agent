# Browser Agent Architecture — Chrome Extension Bridge

> Extends `linkedin-apply-ai-agent` with a Chrome extension bridge so the Claude Agent SDK
> can fill job application forms on behalf of users **without ever receiving their credentials
> or running a server-side browser**.

---

## 1. Design principles

| Principle | Decision |
|---|---|
| No credentials on server | Extension owns the browser session; server never sees LinkedIn cookies or passwords |
| ATS-agnostic from day one | DOM parse is primary; LLM vision is fallback — same agent works on any ATS |
| Sensitive data never in LLM context | Placeholder substitution in the bridge layer before tool results reach the agent |
| Agent loop lives server-side | Claude Agent SDK runs on the existing VPS; extension is a dumb actuator |
| Auto-apply is a global user flag | `user.auto_apply: bool` — no per-job complexity for v1 |
| Human checkpoint is opt-out, not opt-in | HITL preview fires unless `auto_apply=true` |

---

## 2. Component map

```
┌─────────────────────────────────────────────────────────────────────────┐
│  USER LAYER                                                             │
│  SvelteKit HITL UI          Extension popup        LinkedIn tab         │
│  (approve / retry)          (status · pause)       (Easy Apply modal)   │
└──────────────┬──────────────────────┬──────────────────────┬───────────┘
               │                      │                      │
┌──────────────▼──────────────────────▼──────────────────────▼───────────┐
│  CHROME EXTENSION (MV3)                                                 │
│                                                                         │
│  Background service worker          Content script                      │
│  · WebSocket bridge (outbound)      · DOM serialize → field schema      │
│  · JWT stored in chrome.storage     · fill_field via dispatchEvent      │
│  · message router to tabs           · screenshot capture (vision fb)    │
│                                                                         │
│  Extension storage                                                      │
│  · JWT (chrome.storage.session)     · placeholder_map per session       │
└──────────────┬──────────────────────────────────────────────────────────┘
               │  WebSocket (JSON-RPC, JWT in first frame)
┌──────────────▼──────────────────────────────────────────────────────────┐
│  FASTAPI BACKEND (existing, Docker Compose on VPS)                      │
│                                                                         │
│  WS relay                Apply workflow          Bridge MCP server      │
│  · session_id→user_id    · LangGraph states      · tool definitions     │
│  · fan-out to tabs       · auto_apply branch     · placeholder swap     │
│                                                                         │
│  HITL API (existing)     Langfuse                SQLite / Piccolo       │
│  · /hitl/{id}/decide     · traces · cost         · job · cv · state     │
└──────────────┬──────────────────────────────────────────────────────────┘
               │  in-process MCP (stdio)
┌──────────────▼──────────────────────────────────────────────────────────┐
│  AI LAYER                                                               │
│                                                                         │
│  Claude Agent SDK          Form fill agent         CV context           │
│  · agent loop              · read_form_state        · tailored per job  │
│  · tool dispatch           · fill_field ×N          · injected as docs  │
│  · subagent for vision     · advance_step                               │
│                            · submit_form                                │
│                                                                         │
│  Anthropic API             Vision subagent (fallback)                   │
│  · Sonnet 4.6              · spawned when DOM parse score < threshold   │
│  · tool_use blocks         · screenshot → field positions → fills       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. New files and where they live

```
linkedin-apply-ai-agent/
│
├── extension/                          ← NEW: Chrome MV3 extension
│   ├── manifest.json                   · MV3, permissions: tabs, storage, scripting
│   ├── background.js                   · WS bridge, JWT auth, message router
│   ├── content_script.js               · DOM serialize, fill, screenshot
│   └── popup/                          · Status UI (SvelteKit or plain HTML)
│
├── src/
│   ├── workflows/
│   │   └── apply_workflow.py           · MODIFIED: add ApplyState, auto_apply branch
│   │
│   ├── agent/                          ← NEW: agent layer
│   │   ├── form_fill_agent.py          · Claude Agent SDK loop
│   │   ├── tools/
│   │   │   ├── read_form_state.py      · calls bridge, returns FormState
│   │   │   ├── fill_field.py           · dispatches fill via bridge
│   │   │   ├── advance_step.py         · click Next, wait, check for errors
│   │   │   ├── submit_form.py          · click Submit, capture confirmation
│   │   │   └── upload_file.py          · resume PDF upload via file input injection
│   │   ├── vision_subagent.py          · fallback: screenshot → structured fills
│   │   └── placeholder.py             · PII → {{TOKEN}} swap / reverse swap
│   │
│   ├── bridge/                         ← NEW: WebSocket + MCP plumbing
│   │   ├── ws_relay.py                 · FastAPI WebSocket endpoint, session registry
│   │   ├── mcp_server.py               · in-process MCP server wrapping bridge tools
│   │   └── session_store.py            · session_id → user_id, placeholder_map
│   │
│   └── models/                         · MODIFIED: add auto_apply: bool to User
│
└── docs/
    └── ARCHITECTURE-browser-agent.md   ← this file
```

---

## 4. LangGraph apply workflow states

```
INIT
  │  open_easy_apply(job_url) → extension
  ▼
FORM_OPEN
  │  read_form_state → {fields[], step: N/M, ats_type}
  ▼
FILLING  ◄──────────────────────────────────┐
  │  fill_field × N  (placeholder values)   │
  │  advance_step                           │  next page
  │    ├─ validation_errors? → retry fills  │
  │    └─ step < total?  ─────────────────►─┘
  │
  ▼
AWAITING_APPROVAL          (skipped if auto_apply=true)
  │  emit filled_preview to extension popup / HITL UI
  │  wait for user confirm / edit / abort
  ▼
SUBMITTING
  │  submit_form → confirmation screenshot
  │  write applied=true to DB
  ▼
DONE / ERROR
```

**State persisted in LangGraph checkpointer (SQLite)** so a crashed loop can resume
from the last completed step rather than restarting from scratch.

---

## 5. Bridge MCP tools (full list)

All tools are defined with `@tool` in `src/agent/tools/` and registered via
`create_sdk_mcp_server()`. The bridge calls the WS relay which forwards to the
content script in the target tab.

| Tool | Input | Output | Notes |
|---|---|---|---|
| `read_form_state` | `session_id` | `FormState` (fields, step, total, ats_hint) | Tries DOM parse first; sets `vision_needed=true` if score < 0.6 |
| `fill_field` | `session_id, selector, placeholder_value` | `ok \| error` | Bridge reverse-swaps placeholder before sending to extension |
| `advance_step` | `session_id` | `{advanced: bool, errors: FieldError[]}` | Clicks Next, waits 600 ms, re-reads error DOM |
| `upload_file` | `session_id, field_selector, file_key` | `ok \| error` | `file_key` maps to pre-generated PDF path on server |
| `submit_form` | `session_id` | `{confirmed: bool, screenshot_b64}` | Captures confirmation banner or error |
| `take_screenshot` | `session_id, tab_id` | `screenshot_b64` | Used by vision subagent when `vision_needed=true` |
| `await_user_approval` | `session_id, preview_payload` | `{approved, edits[]}` | No-op (returns approved=true immediately) when `auto_apply=true` |

---

## 6. DOM parse → vision fallback decision

```python
def parse_form(dom_snapshot: str) -> tuple[FormState, float]:
    """
    Returns (FormState, confidence_score 0.0–1.0).
    confidence is based on: number of fields found, all fields have selectors,
    no ambiguous label collisions.
    """

# In read_form_state tool:
state, score = parse_form(dom_snapshot)
if score < 0.6 or len(state.fields) == 0:
    # spawn vision subagent
    screenshot = await take_screenshot(session_id)
    state = await vision_subagent.extract_fields(screenshot, cv_context)
    state.vision_needed = True
```

The vision subagent uses Claude's `computer_use` tool (or a structured prompt with
the screenshot as an image block) and returns the same `FormState` schema, so the
main agent loop is unaware of the fallback.

---

## 7. Placeholder substitution — data flow

```
Extension                    Bridge MCP server              Claude Agent SDK
────────────────────────     ──────────────────────         ────────────────
DOM field: value="07123…"    placeholder_map = {            FormState received:
                               PHONE: "07123456789"          { label: "Phone",
                               EMAIL: "k@k.com"               value: "{{PHONE}}"
                             }                                selector: "#phone" }
                                                                    │
                             serialize: replace real value          │ agent decides:
                             with {{PHONE}} token           ◄───────┘ fill_field(
                                                                        "{{PHONE}}",
                                                                        "#phone")
                             reverse swap:                          │
                             "{{PHONE}}" → "07123456789"    ◄───────┘
                                    │
                             send to content script
                             content_script.fill("#phone", "07123456789")
```

Tokens are stored only in `session_store` (in-memory dict keyed by `user_id`),
never written to DB, never appear in Langfuse traces, never sent to Anthropic.

---

## 8. Extension auth flow

```
First-time setup:
1. User opens yourapp.com — already logged in, JWT in localStorage
2. Clicks "Connect extension" → opens /extension-auth page
3. Page calls chrome.runtime.sendMessage({type:"SET_TOKEN", token: jwt})
4. Background SW stores token in chrome.storage.session (clears on browser close)
5. SW opens WebSocket to wss://yourapp.com/ws/extension
   with first frame: {"type":"auth","token":"<jwt>"}
6. WS relay validates JWT, binds session_id → user_id, sends {"type":"ready"}

Reconnect (browser restart):
- User must re-authenticate via /extension-auth (session storage is cleared)
- Consider: if auto_apply=true, prompt on browser open via extension popup
```

---

## 9. Auto-apply branch

`user.auto_apply` is a boolean on the existing User model. The apply workflow checks
it at two points:

```python
# In apply_workflow.py

def should_await_approval(state: ApplyState) -> bool:
    return not state.user.auto_apply

# LangGraph conditional edge:
workflow.add_conditional_edges(
    "FILLING",
    should_await_approval,
    {
        True:  "AWAITING_APPROVAL",   # HITL path
        False: "SUBMITTING",          # auto-apply path — skip preview
    }
)
```

When `auto_apply=True`:
- `await_user_approval` tool returns `{approved: True}` immediately
- The agent proceeds straight to `submit_form`
- A push notification (or email) is sent post-submission with the confirmation screenshot

The flag is toggled via an existing user settings endpoint — no new API surface needed.

---

## 10. New API endpoints

Only two new endpoints are required; everything else reuses the existing HITL API.

| Method | Path | Description |
|---|---|---|
| `WS` | `/ws/extension` | Extension WebSocket connection; JWT in first frame |
| `POST` | `/api/users/me/settings` | Toggle `auto_apply` (extends existing settings endpoint) |

The existing `/api/hitl/{job_id}/decide` with `decision=approve` already triggers
the apply workflow — no change needed there.

---

## 11. Build order (suggested)

**Phase 1 — plumbing (no agent yet)**
1. `extension/background.js` — WS connect + JWT auth frame
2. `extension/content_script.js` — `serialize_form()` only (returns JSON to console)
3. `src/bridge/ws_relay.py` — accept connection, echo messages back
4. Verify: approve a job → WS message reaches the tab → form schema appears in logs

**Phase 2 — DOM path**
5. `src/agent/tools/read_form_state.py` — call bridge, return `FormState`
6. `src/agent/tools/fill_field.py` + `advance_step.py` + `submit_form.py`
7. `src/agent/placeholder.py` — swap logic
8. `src/agent/form_fill_agent.py` — wire Claude Agent SDK loop with tools
9. `src/workflows/apply_workflow.py` — integrate agent into LangGraph states
10. Test end-to-end on a real LinkedIn Easy Apply (3-step form)

**Phase 3 — vision fallback**
11. `src/agent/vision_subagent.py` — screenshot → FormState via computer_use
12. Trigger on confidence < 0.6; verify same FormState schema flows into main loop
13. Test on a Greenhouse or Workday form (DOM structure completely different)

**Phase 4 — auto-apply**
14. Add `auto_apply: bool` to User model migration
15. Conditional edge in LangGraph workflow
16. Settings endpoint toggle
17. Post-submission notification (email or Telegram)

---

## 12. Key risks and mitigations

| Risk | Mitigation |
|---|---|
| LinkedIn DOM changes break serializer | Confidence score + vision fallback; monitor score distribution in Langfuse |
| Agent fills wrong field (hallucination) | HITL preview catches it in non-auto mode; Langfuse trace shows tool calls |
| PII leak via Langfuse traces | Placeholder tokens in all tool call logs; real values never leave bridge memory |
| WebSocket drops mid-apply | LangGraph checkpointer allows resume from last completed state |
| Extension tab loses focus during fill | Content script re-queries selector on each fill; no assumed state between calls |
| LinkedIn rate-limiting / bot detection | Real browser session + native events (not CDP); add random delay between fills |
| auto_apply submits a bad application | Post-submission screenshot notification; user can withdraw manually |
