# Feature Specification: Telegram Job Notifications

## Overview
- **Feature**: Telegram bot that pushes high-scoring, review-ready jobs to a user's Telegram chat with inline Approve/Decline actions
- **Status**: Draft
- **Created**: 2026-08-04
- **Author**: User + Claude Code

## Problem Statement

Jobs that pass the LLM filter and get a tailored CV land in `BusinessState.PENDING` and wait
in the HITL review queue at `/`. Nothing tells the user they are there — the user has to
remember to open the web app. LinkedIn postings decay fast (the most valuable ones are hours
old), so a queue nobody looks at is a queue that produces stale applications.

The system already has a persistent in-app notification tier (`NotificationTable`,
`/api/notifications`, the bell in the header) and a Resend-backed **admin** alert channel
(`AdminAlertService`). Neither reaches the user where they actually are. Telegram is a push
channel the user already has on their phone, and its inline keyboards make an approve/decline
decision a single tap — no login, no browser.

## Goals & Success Criteria

- A user links their Telegram account once, from Settings, and thereafter receives one Telegram
  message per job that becomes review-ready and scores at or above their warning threshold.
- Approve and Decline are actionable directly from the message; the decision goes through the
  same `HITLProcessor.process_decision` path as the web UI, so state-machine rules, per-job
  locking, and ownership checks are identical.
- Exactly one message per job, ever — no duplicates from retries, workflow re-entry, or restarts.
- Notification delivery never affects job processing: a Telegram failure cannot fail a workflow.
- **Success metrics**:
  - Median time from `PENDING` to a HITL decision drops (currently bounded by "when the user
    next opens the app").
  - Share of `PENDING` jobs decided within 24h rises.
  - Zero workflow failures attributable to the Telegram path.

## User Stories

1. As a job seeker, I want a phone notification when a strong match is ready to review, so that
   I can apply while the posting is fresh.
2. As a job seeker, I want to approve or decline straight from the notification, so that a
   decision costs one tap instead of a browser session.
3. As a job seeker, I want only good matches to ping me, so that Telegram stays useful rather
   than noisy.
4. As a job seeker, I want to link Telegram by entering my own `@handle` in Settings, so that I
   don't have to shuttle tokens around.
5. As an admin, I want to see how many users are linked and whether delivery or the webhook is
   broken, so that I can spot a dead integration before users complain.

## Functional Requirements

### Core Capabilities

**FR-1 — Eligibility gate.** A Telegram message is sent for a job only when *all* hold:
1. Telegram is globally configured (`TELEGRAM_BOT_TOKEN` set) and `TELEGRAM_NOTIFICATIONS_ENABLED`.
2. The job just reached `BusinessState.PENDING` (full mode; MVP-mode `COMPLETED` jobs never notify).
3. `job.telegram_notified_at IS NULL` (idempotency).
4. The owning user has a link row with a non-null `chat_id` and `enabled = true`.
5. `job.filter_result` exists and `filter_result.score >= threshold`, where `threshold` is
   `user.filter_preferences.warning_threshold` when the user has filter preferences, else
   `settings.job_filter_warning_threshold` (default 70).

Consequence of (5), stated explicitly: **jobs with no `filter_result` never notify.** That means
manually-submitted and URL-submitted jobs are silent — only the LinkedIn pipeline, which runs the
filter, produces Telegram messages. This is intended; a user who pasted a URL is already at the
keyboard.

**FR-2 — One message per job.** On a successful send, `job.telegram_notified_at` is stamped. The
retry workflow returns a job to `PENDING`, and the recovery/re-queue paths can re-run preparation;
neither re-notifies, because the stamp survives. The stamp is only ever written after Telegram
confirms the send, so a failed send leaves the job eligible for a later attempt (which in practice
means the next time that job transitions into `PENDING`).

**FR-3 — Message content.** HTML-formatted, single message:

```
🎯 <b>Senior Backend Engineer</b>
🏢 Acme Corp · 📍 Berlin (Remote)
📊 Match score: <b>82</b>/100
⚠️ Red flags: on-call rotation; equity in lieu of salary
```

Red-flag line is omitted when `filter_result.red_flags` is empty; at most 3 flags are listed.
Job title, company, and location are truncated defensively (Telegram's 4096-char message cap is
not a realistic risk, but a pathological scraped title is).

Inline keyboard, two rows:
- Row 1: `✅ Approve` (`callback_data="a:<token>"`), `❌ Decline` (`callback_data="d:<token>"`)
- Row 2: `🔗 Open in app` (`url="{APP_URL}/?job={job_id}"` — the existing review page already
  reads the `job` query param and selects that card)

**Retry is deliberately not a button.** Retry requires free-text feedback
(`HITLProcessor.process_decision` raises `ValueError` without it), which would need a
conversational state machine in the bot. Users retry in the web UI via the "Open in app" link.

**FR-4 — Linking (username allowlist).** The Bot API cannot address a user by username — it only
sends to a `chat_id`, and only after the user has messaged the bot. So the username is an
**allowlist**, not an address:

1. User enters their Telegram `@handle` in Settings → Telegram. The backend stores it as
   `{user_id, username, chat_id: null}` (status `awaiting_start`).
2. User opens `t.me/<bot_username>` and sends `/start`.
3. The bot compares the update's `from.username` (case-insensitively) against claimed usernames.
   On a match with a row whose `chat_id` is null, it binds `chat_id`, sets `linked_at`,
   `enabled = true`, and replies with a confirmation naming the threshold.
4. Once bound, the username is **locked to that chat**: a `/start` from a different chat with the
   same username is rejected with "already linked to another chat — unlink in Settings first."
   This is the mitigation for username squatting and Telegram handle reuse (handles are
   user-changeable and get recycled). A user who legitimately changes devices/accounts unlinks in
   Settings, which deletes the row, and re-links.

A username may be claimed by only one app user at a time; a second user claiming a taken handle
gets a 409.

**FR-5 — `/start` edge cases.**
| Situation | Bot reply |
|---|---|
| Update has no `from.username` | "Set a Telegram username in Telegram's settings first, then send /start again." |
| Username not claimed by any user | "This Telegram account isn't linked. Add your @handle in Settings → Telegram, then send /start." |
| Claimed, `chat_id` null | "✅ Linked as `<email>`. You'll get a message when a job scoring ≥ N is ready to review." |
| Claimed, `chat_id` == this chat | "Already linked." (also re-enables a `broken` link) |
| Claimed, `chat_id` != this chat | "That handle is already linked to a different Telegram chat. Unlink in Settings to re-link." |
| Any other message/command | Short help text naming `/start`. |

**FR-6 — Inline action handling.** On a `callback_query`:
1. Resolve `link = get_by_chat_id(update.callback_query.message.chat.id)`. No link or
   `enabled = false` → `answerCallbackQuery("Not linked")`, stop.
2. Parse `data` as `a:<token>` / `d:<token>`; resolve the job by `telegram_callback_token`.
   Unknown token → `answerCallbackQuery("This job is no longer available")`.
3. **Authorization**: require `job.user_id == link.user_id`. Mismatch → treated as unknown
   ("no longer available"); logged at WARNING. The `chat_id → user_id` binding is the only
   identity the bot trusts — never anything inside `callback_data`.
4. Call `HITLProcessor.process_decision(job_id, HITLDecision(decision="approved"|"declined",
   reasoning="Declined from Telegram" for declines), user_id=link.user_id)`.
5. Map the outcome: success → `answerCallbackQuery("Approved ✅"/"Declined")` and
   `editMessageText` appending an outcome line **with the keyboard removed**; `RuntimeError`
   (job not `PENDING`) → "Already decided" plus keyboard removal; `KeyError` → "Job not found".

Note on declines: the auto-refiner only captures a decline signal when a *reason* is given
(`_handle_decline` requires non-empty `decision.reasoning`). A Telegram decline sends the fixed
string `"Declined from Telegram"`, which technically satisfies that check but carries no
information. **Send `reasoning=None` for Telegram declines** so the refiner is not fed junk
signals; users who want to teach the filter decline in the web UI with a real reason. This is a
deliberate trade-off, called out in Design Trade-offs below.

**FR-7 — Failure handling and auto-disable.** All sends are best-effort: exceptions are logged
and swallowed, never propagated into a workflow (same contract as the refinement notification in
`src/services/jobs/refinement.py`).
- `429` → one retry honoring `retry_after` (capped at 5s), then give up.
- Network error / `5xx` → one retry, then give up.
- `403` (bot blocked by user) or `400` with `chat not found` / `chat_id is empty` → set
  `enabled = false` and record `last_error`; the link's derived status becomes `broken` and the
  Settings UI shows it as such. This stops the system from hammering a dead chat forever.
- Any other `4xx` → log with the API `description`, leave the link alone.

**FR-8 — Webhook transport.** Updates arrive at `POST /api/telegram/webhook`, proxied by the
existing Caddy `handle /api/*` block. The route:
- Returns `404` when Telegram is not configured (mirrors the `dev-login` pattern).
- Requires the `X-Telegram-Bot-Api-Secret-Token` header to equal `TELEGRAM_WEBHOOK_SECRET`,
  compared with `secrets.compare_digest`; mismatch → `403`, logged at WARNING.
- Returns `200 {"ok": true}` for everything it handles *or ignores*, including on internal
  errors (which are logged). Non-2xx makes Telegram retry with backoff and eventually complain;
  for a convenience notification, a retry storm is worse than a dropped update.
- Ignores any update kind other than `message` and `callback_query`.

At startup, if `TELEGRAM_WEBHOOK_URL` is set, the app calls `setWebhook` with the secret token
(non-blocking, failure logged not fatal — same posture as the model-catalog load). Local
development uses a tunnel (cloudflared/ngrok) and a **separate bot token**; two environments must
never share a token, since `setWebhook` is global per bot.

**FR-9 — Settings UI.** A `TelegramSection.svelte` card in the existing Settings page showing:
- `@handle` input with Save, and the derived link status: `unlinked` / `awaiting_start` /
  `linked` / `broken`.
- When `awaiting_start`: the `t.me/<bot_username>` link (and the literal instruction to send
  `/start`), plus a "Check status" refresh.
- When `linked`: the bound chat's confirmation, an enable/disable toggle, and Unlink.
- When `broken`: the `last_error` and an explanation ("you may have blocked the bot"), with
  re-link instructions.
- The whole card is hidden when the API reports Telegram is not configured server-side.

**FR-10 — Admin observability.** `GET /api/admin/telegram` returns `{configured,
bot_username, notifications_enabled, linked_count, awaiting_count, broken_count,
broken_links: [{user_id, email, username, last_error}], webhook: <getWebhookInfo() payload or
error>}`. Surfaced as a card on the existing `/admin/queue` page. `getWebhookInfo` exposes
Telegram's own `last_error_message` and `pending_update_count`, which is the fastest way to see a
misconfigured webhook.

### User Flows

**Flow A — Linking**
```
Settings → Telegram card → type "@kule" → Save
   POST /api/users/me/telegram {username: "kule"}
   → row {user_id, username: "kule", chat_id: null}   status: awaiting_start
User taps t.me/<bot> → /start
   POST /api/telegram/webhook  (message, from.username = "kule")
   → bind chat_id, linked_at=now, enabled=true        status: linked
   → bot replies "✅ Linked as user@example.com …"
Settings card polls/refreshes → shows "linked"
```

**Flow B — Notification and decision**
```
LinkedIn scheduler → queue → WorkflowDispatcher.dispatch_preparation
   preparation workflow: extract → filter (score 82) → compose CV → PDF → save_to_db (PENDING)
dispatcher, after successful ainvoke → TelegramNotifier.notify_job_ready(job_id, user_id)
   gates pass (82 ≥ 70, link live, telegram_notified_at null)
   → sendMessage + inline keyboard
   → stamp job.telegram_notified_at
User taps ✅ Approve
   POST /api/telegram/webhook (callback_query, data="a:Xk3p9Qa2")
   → chat_id → link → user_id;  token → job;  ownership verified
   → HITLProcessor.process_decision(job_id, approved, user_id) → APPROVED
   → answerCallbackQuery("Approved ✅") + editMessageText (keyboard removed)
```

**Flow C — User blocks the bot**
```
sendMessage → 403 Forbidden: bot was blocked by the user
   → link.enabled = false, last_error recorded         status: broken
   → job.telegram_notified_at stays NULL (no send happened)
   → no further sends attempted for that user
Settings card shows "broken" with re-link instructions
```

### Data Model

New module `src/models/telegram.py`:

```python
class TelegramLinkStatus(StrEnum):
    UNLINKED = "unlinked"                # no row
    AWAITING_START = "awaiting_start"     # username claimed, chat_id is None
    LINKED = "linked"                     # chat_id bound, enabled
    BROKEN = "broken"                     # chat_id bound, enabled=False (403/not-found)

class TelegramLink(BaseModel):
    user_id: str
    username: str                  # normalized: lowercased, leading '@' stripped
    chat_id: int | None = None
    enabled: bool = True
    linked_at: datetime | None = None
    last_error: str | None = None
    last_notified_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def status(self) -> TelegramLinkStatus: ...   # derived, never stored

class TelegramLinkUpdateRequest(BaseModel):
    username: str = Field(min_length=5, max_length=32)   # validated /^@?[A-Za-z0-9_]{5,32}$/
    enabled: bool | None = None
```

New Piccolo table in `src/services/db/tables.py`:

```python
class TelegramLinkTable(Table, tablename="telegram_link"):
    user_id = Varchar(length=36, primary_key=True)   # one link per user
    username = Varchar(length=32, unique=True, index=True)  # lookup path for /start
    chat_id = BigInt(null=True, unique=True, index=True)    # lookup path for callback_query
    enabled = Boolean(default=True)
    linked_at = Timestamptz(null=True)
    last_error = Text(null=True)
    last_notified_at = Timestamptz(null=True)
    created_at = Timestamptz()
    updated_at = Timestamptz()
```

Two new columns on `Job`:

```python
telegram_notified_at = Timestamptz(null=True)          # idempotency stamp
telegram_callback_token = Varchar(length=16, null=True, index=True)  # short callback handle
```

Corresponding fields on `JobRecord` (`src/models/unified.py`), both optional, and both mapped in
the SQLite row↔model conversion.

**Why a callback token instead of the job id.** `callback_data` is capped at **64 bytes** by the
Bot API, and `Job.job_id` is `Varchar(80)` — LinkedIn ids are composite (`linkedin_id:user_id`).
Real ids land near 47 chars and would fit today, but a longer id silently yields a
`BUTTON_DATA_INVALID` 400 at send time. A random 8-char `secrets.token_urlsafe(6)` token makes
`callback_data` 10 bytes with no ceiling risk, and gives the handler a natural "this came from a
message we actually sent" check. It is not a security boundary — authorization is the
`chat_id → user_id` binding.

### Integration Points

| Touchpoint | Change |
|---|---|
| `src/agents/dispatcher.py` | After a successful `prep_workflow.ainvoke`, call `ctx.telegram_notifier.notify_job_ready(job_id, user_id)` inside a `try/except` that only logs. **`dispatch_retry` is deliberately not hooked** — a retried job returning to `PENDING` must not re-ping. |
| `src/context.py` | New optional `telegram_link_repository`, `telegram_client`, `telegram_notifier`, `telegram_bot_service` fields; constructed in `create_app_context` only when a bot token is configured. |
| `src/api/main.py` | Include `telegram` router; in `lifespan`, fire-and-forget `setWebhook` when `TELEGRAM_WEBHOOK_URL` is set; close the shared `httpx.AsyncClient` on shutdown. |
| `src/services/auth/user_repository.py` + `src/services/db/sqlite_repository.py` | Bind `TelegramLinkTable._meta._db` to the shared engine and add `create_table(if_not_exists=True)` to both `initialize()` paths (same as `NotificationTable`). |
| `src/services/db/migrations.py` | Append `Migration("add_job_telegram_notified_at", _add_column("job", "telegram_notified_at", "telegram_notified_at TIMESTAMP NULL"))` and `Migration("add_job_telegram_callback_token", _add_column("job", "telegram_callback_token", "telegram_callback_token VARCHAR(16) NULL"))`. |
| `src/api/routes/users.py` | `GET/PUT/DELETE /api/users/me/telegram`. |
| `src/api/routes/admin.py` | `GET /api/admin/telegram`. |
| `src/services/db/repository.py` + both implementations | `get_by_telegram_token(token) -> JobRecord \| None`. In-memory does a scan; SQLite uses the indexed column. |
| `ui/src/lib/api/settings.ts`, `ui/src/lib/api/admin.ts` | Typed client methods. |
| `ui/src/lib/components/settings/TelegramSection.svelte`, `ui/src/routes/settings/+page.svelte` | New card, wired in. |
| `ui/src/routes/admin/queue/+page.svelte` | Telegram health card. |

The in-app `NotificationTable` tier is **not** used for these events: the review queue itself is
already the in-app surface, and a bell entry per job would duplicate it.

## Technical Design

### Architecture

Four new service objects under `src/services/notifications/`, alongside the existing
`NotificationRepository`:

```
TelegramClient          — thin async wrapper over the Bot HTTP API (no domain knowledge)
TelegramLinkRepository  — Piccolo CRUD over telegram_link (user_id / username / chat_id lookups)
TelegramNotifier        — outbound: eligibility gate → compose → send → stamp → error policy
TelegramBotService      — inbound: /start binding, callback_query → HITLProcessor
```

The split keeps the HTTP layer mockable (unit tests stub `TelegramClient` or its transport), the
outbound gate independent of the inbound handler, and the FastAPI route a thin adapter — the same
shape as `JobOrchestrator` / `HITLProcessor`.

Wired through `AppContext`, all fields optional and `None` when unconfigured. Every call site
null-checks, exactly as `notification_repository` is treated in `refinement.py`.

### Technology Stack
- **Frameworks**: FastAPI (webhook route), Piccolo ORM (new table + migrations), Pydantic v2 (models)
- **Libraries**: `httpx` (already a dependency) — **no new dependency**
- **Tools**: `@BotFather` for bot creation; Caddy for TLS/proxy; cloudflared or ngrok for local
  webhook development

Rationale for raw `httpx` over `python-telegram-bot` / `aiogram`: the integration needs five
endpoints (`sendMessage`, `editMessageText`, `answerCallbackQuery`, `setWebhook`,
`getWebhookInfo`), and we are handling updates ourselves in a FastAPI route rather than using a
framework's dispatcher. Both libraries want to own an `Application` lifecycle and pull their own
transport pin; neither earns that for five POSTs.

### Data Persistence

SQLite via Piccolo on the shared engine, consistent with everything else.

**Why a dedicated table rather than a JSON column on `UserTable`** (this was the delegated
decision — "if it should be queryable and indexed, create a table"): the webhook resolves
`chat_id → user` on *every* update and `username → user` on every `/start`. Both are lookups on
non-primary-key fields, and both need uniqueness — one Telegram chat must not map to two app
users. A `telegram_preferences` JSON column (the `filter_preferences` pattern) could not be
indexed on those keys and could not enforce uniqueness; the webhook would have to scan every user
row and dedupe in Python. The lookups are genuinely queryable, so the table it is. `user_id` as
the primary key encodes the one-link-per-user invariant in the schema.

The two `Job` columns are additive nullable columns via the existing idempotent migration
framework — no backfill; existing `PENDING` jobs have `telegram_notified_at IS NULL` and are
therefore eligible if they transition again, which is acceptable (they are already stale).

### API / Interface Design

**User-facing (auth required, self-scoped)**

| Method | Endpoint | Behavior |
|---|---|---|
| GET | `/api/users/me/telegram` | `{configured, bot_username, status, username, enabled, linked_at, last_error, min_score}` |
| PUT | `/api/users/me/telegram` | Claim/replace `@handle`, or toggle `enabled`. Changing the username clears `chat_id` (back to `awaiting_start`). `409` if the handle is claimed by another user; `422` on a malformed handle. |
| DELETE | `/api/users/me/telegram` | Unlink — deletes the row. `204`. |

**Webhook (public, secret-token authenticated)**

| Method | Endpoint | Behavior |
|---|---|---|
| POST | `/api/telegram/webhook` | `404` when unconfigured, `403` on bad secret token, otherwise `200 {"ok": true}` always. |

**Admin (`AdminUser`)**

| Method | Endpoint | Behavior |
|---|---|---|
| GET | `/api/admin/telegram` | Health + link counts + broken links + `getWebhookInfo` |

**Service signatures**

```python
class TelegramApiError(Exception):
    status_code: int
    description: str
    retry_after: int | None

class TelegramClient:
    def __init__(self, token: str, *, base_url: str = "https://api.telegram.org",
                 timeout: float = 10.0, client: httpx.AsyncClient | None = None) -> None: ...
    async def send_message(self, chat_id: int, text: str, *,
                           reply_markup: dict | None = None,
                           parse_mode: str = "HTML",
                           disable_web_page_preview: bool = True) -> dict: ...
    async def edit_message_text(self, chat_id: int, message_id: int, text: str, *,
                                reply_markup: dict | None = None,
                                parse_mode: str = "HTML") -> dict: ...
    async def answer_callback_query(self, callback_query_id: str, *,
                                    text: str | None = None,
                                    show_alert: bool = False) -> dict: ...
    async def set_webhook(self, url: str, *, secret_token: str) -> dict: ...
    async def get_webhook_info(self) -> dict: ...
    async def aclose(self) -> None: ...

class TelegramLinkRepository:
    async def get_for_user(self, user_id: str) -> TelegramLink | None: ...
    async def get_by_username(self, username: str) -> TelegramLink | None: ...
    async def get_by_chat_id(self, chat_id: int) -> TelegramLink | None: ...
    async def claim_username(self, user_id: str, username: str) -> TelegramLink: ...   # raises ValueError on conflict
    async def bind_chat(self, user_id: str, chat_id: int) -> TelegramLink: ...
    async def set_enabled(self, user_id: str, enabled: bool, *, error: str | None = None) -> None: ...
    async def touch_notified(self, user_id: str) -> None: ...
    async def unlink(self, user_id: str) -> bool: ...
    async def counts(self) -> dict[str, int]: ...
    async def list_broken(self, limit: int = 50) -> list[TelegramLink]: ...

class TelegramNotifier:
    def __init__(self, ctx: AppContext) -> None: ...
    @property
    def enabled(self) -> bool: ...
    async def notify_job_ready(self, job_id: str, user_id: str) -> bool: ...   # never raises

class TelegramBotService:
    def __init__(self, ctx: AppContext) -> None: ...
    async def handle_update(self, update: dict) -> None: ...   # never raises
```

**New settings** (`src/config/settings.py`)

```python
telegram_bot_token: str | None = None          # from @BotFather; None ⇒ feature off
telegram_bot_username: str | None = None       # for the t.me link in Settings
telegram_webhook_secret: str | None = None     # X-Telegram-Bot-Api-Secret-Token
telegram_webhook_url: str | None = None        # https://<domain>/api/telegram/webhook
telegram_notifications_enabled: bool = True    # kill switch, independent of the token
```

Startup validation: if `telegram_bot_token` is set but `telegram_webhook_secret` is not, log a
loud warning and refuse to register the webhook — an unauthenticated public webhook lets anyone
forge `/start` and callback updates.

## Non-Functional Requirements

- **Performance**: `notify_job_ready` adds one indexed link lookup plus one outbound HTTPS call
  (10s timeout) per eligible job, after the workflow has already completed — it is off the
  critical path and bounded by the timeout. Webhook handling is two indexed lookups plus the
  existing `HITLProcessor` path. `httpx.AsyncClient` is shared and reused (no per-send connection
  setup).
- **Security**:
  - Webhook authenticated by `secrets.compare_digest` on Telegram's secret-token header.
  - Authorization is derived solely from the `chat_id → user_id` binding; `callback_data` is
    treated as untrusted input and never carries identity.
  - Ownership is re-verified (`job.user_id == link.user_id`) before every decision, and
    `process_decision` re-checks under its per-job lock.
  - `TELEGRAM_BOT_TOKEN` is a bearer credential in the URL path — never logged. The client
    redacts the token from any error/exception string.
  - Message bodies contain job titles and companies (not CV content), sent to a third party.
    Worth a line in the Settings UI so the user knows what leaves the system.
  - Username locking (FR-4 step 4) is the squatting mitigation; note in Security review that a
    handle claimed-but-never-linked is theoretically claimable by whoever holds that handle at
    `/start` time. The window is small and the blast radius is "receives job titles"; a user who
    wants zero exposure uses the deep-link flow, which is listed under Open Questions.
- **Observability**:
  - INFO on link bind/unbind, on each send with `job_id`/`user_id`/`score`, on each decision made
    via Telegram.
  - WARNING on secret-token mismatch, ownership mismatch, unknown callback token.
  - ERROR (with `exc_info`) on unexpected send/handler failures.
  - Admin endpoint surfaces aggregate health including Telegram's own `getWebhookInfo` errors.
- **Error Handling**: FR-7. The governing rule: **the Telegram path never raises into a
  workflow, a scheduler, or the queue consumer.** Both `notify_job_ready` and `handle_update`
  catch broadly at their boundary and log.

## Implementation Considerations

### Design Trade-offs

| Decision | Alternative considered | Rationale |
|---|---|---|
| Notify at `PENDING` (post-CV) | Notify right after filter scoring | Every notification is actionable — the CV and PDF exist, so Approve/Decline and the deep link are all meaningful. Costs the CV-composition latency. |
| Reuse `warning_threshold` | Dedicated per-user `min_score` | Zero new config and no new UI. Accepted coupling: raising the threshold to reduce Telegram noise also changes HITL warning badges. If that coupling bites, add `min_score` to a Telegram preferences model later. |
| Username allowlist | Deep-link one-time token | User's explicit preference; no token table, and the handle is something the user already knows. Weaker than a token against squatting/renames, mitigated by chat locking. |
| Webhook | Long polling | No idle polling, instant delivery, and Caddy already proxies `/api/*`. Cost: local dev needs a tunnel and a second bot token. |
| Raw `httpx` | `python-telegram-bot` / `aiogram` | Five endpoints, updates handled in FastAPI. No new dependency, no framework lifecycle to reconcile with `lifespan`. |
| Hook in `WorkflowDispatcher` | Hook inside `save_to_db_node` | Every real path (queue consumer, orchestrator, scheduler, recovery) goes through the dispatcher, which already holds `AppContext`. Keeps workflow nodes free of a notifier dependency threaded through `config["configurable"]`. |
| Short callback token column | `callback_data = f"a:{job_id}"` | 64-byte `callback_data` cap vs an 80-char `job_id` column. Today's ids fit; the failure mode if one doesn't is a silent 400 at send time. One nullable column removes the class of bug. |
| `reasoning=None` on Telegram declines | Fixed `"Declined from Telegram"` string | A constant string would pass the auto-refiner's non-empty-reason check and pollute filter-refinement proposals with content-free signals. Reason-bearing declines stay a web-UI action. |
| Dedicated `telegram_link` table | JSON column on `UserTable` | `chat_id` and `username` are indexed lookup keys on the webhook hot path and need uniqueness; JSON can deliver neither. |
| Best-effort send, no outbox | Persistent outbox with backoff | This is a convenience notification, not a transaction. An outbox means a table, a worker, and a failure state machine for a message whose value expires in hours anyway. |

### Dependencies

- A bot created via `@BotFather` (token + username).
- Production: `TELEGRAM_WEBHOOK_URL` reachable over HTTPS at the existing domain — already
  satisfied by Caddy's `handle /api/*` rule; no Caddyfile change needed.
- Local development: a tunnel and a **separate** bot token (a bot has exactly one webhook).
- `.env` / `.env.example` and the CI-managed prod `.env` need the five new variables.
- No new Python packages.

### Testing Strategy

Unit tests only (integration and E2E explicitly deferred per scope decision). `httpx.MockTransport`
stands in for the Bot API; the SQLite tests follow the in-memory-engine pattern already used by
`tests/unit/test_filter_repository.py`.

- `tests/unit/test_telegram_client.py` — request shape per endpoint, `parse_mode`/keyboard
  serialization, `429` retry honoring `retry_after`, `403` → `TelegramApiError`, token redaction
  in error strings.
- `tests/unit/test_telegram_link_repository.py` — claim/rebind/unlink, `username` and `chat_id`
  uniqueness, username normalization (`@Kule` → `kule`), cross-user claim conflict, derived
  status for each of the four states.
- `tests/unit/test_telegram_notifier.py` — the eligibility gate, one case per rejection reason
  (no token, kill switch off, MVP mode, no link, `awaiting_start`, `enabled=false`, no
  `filter_result`, score below threshold, already notified); happy path stamps
  `telegram_notified_at` and sends once; `403` disables the link and leaves the stamp null;
  transient failure leaves both link and stamp untouched.
- `tests/unit/test_telegram_bot_service.py` — every `/start` row of the FR-5 table; callback
  cases: unknown token, wrong owner, job not `PENDING`, successful approve, successful decline
  (asserting `reasoning is None`), keyboard removed on terminal outcomes.
- `tests/unit/test_telegram_webhook_route.py` — `404` unconfigured, `403` bad/missing secret
  token, `200` on an ignored update kind, `200` when the handler raises internally.
- `tests/unit/test_admin_endpoints.py` (extend) — `/api/admin/telegram` shape and 403 for
  non-admins.

Validation commands: `uv run pytest tests/unit -q`, `uv run ruff check src`, `uv run black src`,
`uv run mypy src`.

## Out of Scope

- Retry-with-feedback from Telegram (needs conversational state; web UI handles it).
- Bot commands beyond `/start` — no `/pending`, `/stats`, `/mute`, `/help` menu.
- Digest/batched messages and quiet hours / rate limiting per user.
- Notifications for any event other than "job became review-ready": no scrape failures, no
  application-submitted confirmations, no filter-refinement proposals.
- Notifications for jobs without a `filter_result` (manual and URL submissions).
- Long-polling fallback when the webhook is unreachable.
- Multiple Telegram chats per user, or group/channel targets.
- Sending the CV PDF as a Telegram document.
- Integration and E2E tests (deferred by explicit scope decision).
- Migrating the admin Resend alerts to Telegram.

## Open Questions

1. **Handle-squatting tolerance.** The allowlist flow has a small window where whoever holds a
   claimed-but-unlinked handle can bind first. Acceptable, or should the deep-link token be added
   later as an alternative path (FR-4 would keep working unchanged)?
2. **Threshold coupling.** If reusing `warning_threshold` proves too noisy or too quiet in
   practice, is adding a per-user `telegram_min_score` a fast follow?
3. **Dev bot token.** Confirm a second `@BotFather` bot for local development, and whether
   `TELEGRAM_WEBHOOK_URL` should simply stay unset locally (bot inbound disabled, outbound sends
   still testable against a real chat).
4. **Approve semantics.** `HITLProcessor._handle_approve` currently only moves the job to
   `APPROVED` — the application workflow is stubs. Should the Telegram confirmation say
   "Approved — application pending" to set expectations, rather than implying it was submitted?

## References

- `src/agents/dispatcher.py` — `dispatch_preparation`, the notification hook point
- `src/agents/preparation_workflow.py:621` — `save_to_db_node`, where `PENDING` is written
- `src/services/jobs/hitl_processor.py` — `process_decision`, `_handle_approve`, `_handle_decline`
- `src/services/jobs/refinement.py:167` — the best-effort notification pattern this follows
- `src/services/notifications/notification_repository.py` — sibling repository conventions
- `src/services/alerts.py` — existing outbound alert channel (admin/Resend)
- `src/services/db/migrations.py` — idempotent additive-column migration framework
- `src/services/db/tables.py` — `NotificationTable` as the model for a new table's wiring
- `src/models/job_filter.py` — `FilterResult.score`, `UserFilterPreferences.warning_threshold`
- `ui/src/routes/+page.svelte:20` — the review page's `?job=<id>` deep link
- `Caddyfile` — `handle /api/*` block the webhook rides on
- [Telegram Bot API — `sendMessage`, `answerCallbackQuery`, `setWebhook`](https://core.telegram.org/bots/api)
