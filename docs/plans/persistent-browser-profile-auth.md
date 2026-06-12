# Plan: Persistent Browser Profile for LinkedIn Auth

**Status:** Draft
**Date:** 2026-06-01
**Owner:** kirill
**Related:** commit `6fabda6` (per-job session auth observability), `scripts/capture_linkedin_cookies.py`, `src/services/linkedin/browser_automation.py`

---

## 1. Problem & Root Cause

The current auth model — capture cookies in a headed browser (`capture_linkedin_cookies.py`),
`scp` the JSON jar to the VPS, and inject it into a fresh Playwright context via
`context.add_cookies()` — **no longer produces an authenticated scrape.**

Verified empirically on 2026-06-01 with a freshly-captured cookie jar:

- The cookie file on the VPS is valid: `li_at` present (152 chars, expires 2027-06-01), loaded by the scraper (`Loaded 14 cookies` in logs).
- The browser context **does send `li_at`** to `https://www.linkedin.com/feed/` — yet LinkedIn **302-redirects to `/login`**.
- Job detail pages render the **guest JSERP layout** (0 of 4 authenticated SDUI markers, 15 "Sign in" buttons, no logged-in global-nav). The guest-only selector `div.show-more-less-html__markup` matches, confirming the layout is genuinely public.
- Reproduced in **both headless and headed** replay, from the **VPS exit IP** — so it is neither a headless-detection issue nor an IP-mismatch issue.

**Conclusion:** LinkedIn binds an authenticated session to more than the bare `li_at`
cookie (device/browser fingerprint + ancillary client state). A token exported from
the live login browser and injected into a *separate* context is treated as
logged-out. The `session_authenticated=False` banner is **accurate**, not a detector
bug. Re-running the cookie refresh does not help — the freshly-captured token is
rejected on replay.

> Note: the pipeline still produces output today only because guest JSERP returns
> full public descriptions for most jobs. Auth-gated descriptions/fields come back
> empty, and the planned LinkedIn Easy Apply automation cannot work at all while we
> are effectively a guest.

## 2. Goal & Non-Goals

**Goal:** Restore a genuinely authenticated LinkedIn session for the scraper, such
that `/jobs/view/<id>` renders the authenticated SDUI layout and
`session_authenticated=True` is recorded.

**Non-goals:**
- Implementing Easy Apply (separate work; this unblocks it).
- Changing the layout-detection / observability logic — it is correct.
- Automating credential+2FA login headlessly (LinkedIn challenges make this unreliable; interactive login stays).

## 3. Why the fix is "persist the whole session, not the cookie"

The replay failure is about **session binding**. The robust fixes, cheapest first:

| Artifact persisted | Captures | Cross-platform safe? | Likely sufficient? |
|---|---|---|---|
| Cookie jar (today) | cookies only | ✅ (plain JSON) | ❌ proven to fail |
| `storage_state` | cookies **+ localStorage** | ✅ (plain JSON) | ❓ unknown — depends if binding lives in localStorage |
| Persistent profile (`user_data_dir`) | cookies + localStorage + IndexedDB + full Chromium profile | ⚠️ **macOS→Linux cookie encryption breaks** (see §6) | ✅ if login & scrape run on same OS |

This is why **Phase 0 is a validation experiment** — we determine the *minimal*
artifact that actually restores auth before committing to the heavier
persistent-profile architecture.

## 4. Approach

### Phase 0 — Validate the minimal sufficient artifact (no code merged, ~30 min)

Run all tests under the SOCKS tunnel (local traffic exits via VPS IP) to mirror
scraper network conditions.

1. **`storage_state` replay (cheapest, cross-platform safe).**
   - Capture: headed login, then `await context.storage_state(path="li_state.json")` (includes cookies + per-origin localStorage).
   - Replay: `browser.new_context(storage_state="li_state.json")` **headless**, navigate `/feed`. Authenticated (no `/login` redirect) ⇒ **storage_state is enough**. This becomes the chosen approach — minimal change, no profile-lock or transplant problems.

2. **Persistent profile, same-OS replay (fallback validation).**
   - Capture: `launch_persistent_context(user_data_dir=A)` headed, log in, close.
   - Replay: `launch_persistent_context(user_data_dir=A)` **headless** (same machine), navigate `/feed`. Authenticated ⇒ persistent profile works *when login and scrape share an OS*.

**Decision rule:**
- Test 1 passes → implement **Approach A (storage_state)**. Stop.
- Test 1 fails, Test 2 passes → implement **Approach B (persistent profile)** + on-VPS login (§6).
- Both fail → escalate; the binding is runtime fingerprint-based (canvas/WebGL) and we need a same-fingerprint long-lived session (single browser that logs in and scrapes without restart).

### Approach A — `storage_state` (preferred if Phase 0 Test 1 passes)

Smallest change; keeps the capture→scp→load operational shape.

- **`capture_linkedin_cookies.py`**: after auth confirmed, write `data/linkedin_state.json` via `context.storage_state(path=...)` instead of dumping `context.cookies()`.
- **`browser_automation.py`**: replace `add_cookies()` with passing `storage_state=<path>` to `new_context()`:
  ```python
  context_kwargs = {"viewport": {...}, "locale": "en-US"}
  if self.storage_state_path.exists():
      context_kwargs["storage_state"] = str(self.storage_state_path)
  self.context = await self.browser.new_context(**context_kwargs)
  ```
  `_load_cookies()` is retired (or kept as a legacy fallback). `storage_state` JSON is plain text → `scp` transplant stays cross-platform safe.
- **Ops**: identical to today — `scp data/linkedin_state.json` to the VPS, restart API.

### Approach B — Persistent `user_data_dir` profile (if Phase 0 forces it)

More robust but heavier; requires login on the VPS (§6) to avoid the macOS→Linux
cookie-encryption problem.

**`src/config/settings.py`** — add:
```python
linkedin_user_data_dir: str | None = None  # e.g. ./data/li_profile ; when set, use a persistent profile
```

**`src/services/linkedin/browser_automation.py`** — `initialize()` branches on the setting:
```python
async def initialize(self) -> None:
    self._playwright = await async_playwright().start()
    try:
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("DYLD_")}
        viewport = {"width": random.randint(1280, 1920), "height": random.randint(800, 1080)}
        if self.user_data_dir:
            self.user_data_dir.mkdir(parents=True, exist_ok=True)
            kwargs = {"headless": self.headless, "env": clean_env,
                      "viewport": viewport, "locale": "en-US"}
            if self.proxy_server:
                kwargs["proxy"] = {"server": self.proxy_server}
            self.context = await self._playwright.chromium.launch_persistent_context(
                str(self.user_data_dir), **kwargs)
            self.browser = self.context.browser  # may be None for persistent contexts
        else:
            # legacy launch + new_context path (unchanged)
            ...
        await self._stealth.apply_stealth_async(self.context)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
    except Exception:
        await self.close()
        raise
```

`is_alive()` and `close()` must tolerate `self.browser is None`:
```python
async def close(self):
    try:
        if self.context is not None:
            await self.context.close()      # flushes persistent profile to disk
        elif self.browser is not None:
            await self.browser.close()
    finally:
        self.browser = self.context = self.page = None
    if self._playwright:
        await self._playwright.stop(); self._playwright = None
```
`ensure_authenticated()`: when a persistent profile is configured, **skip
`_load_cookies()`** — the profile already carries the session.

**`capture_linkedin_cookies.py`**: launch `launch_persistent_context(user_data_dir, headless=False)`, log in, confirm `/feed`, `await context.close()`. The profile dir *is* the artifact.

## 5. Concurrency / Profile-lock (Approach B only)

A Chromium `user_data_dir` is **locked to one process**. The API holds the scraper's
persistent context open for its whole lifetime, so:

- The capture login **cannot** run against the same live profile while the API is up.
- Two call sites construct browsers (`api/main.py:148` scheduler + `api/routes/jobs.py:93` manual search). With a shared profile they must **not** run concurrently. Today they're separate short-lived instances; under a persistent profile we should funnel both through a single long-lived `ctx.browser` (already stored on `AppContext`) and have the manual-search route reuse `ctx.browser` instead of constructing its own.

**Action (Approach B):** make `jobs.py` manual search reuse `ctx.browser` when present;
only construct a throwaway browser when the scheduler is disabled. Guard against
concurrent search cycles with the existing scheduler lock.

(Approach A has none of this — `storage_state` is read-once into independent contexts.)

## 6. Cross-platform transplant problem (Approach B only)

Playwright Chromium encrypts the profile's `Cookies` SQLite with an OS-specific key
(macOS Keychain vs Linux "peanuts"/gnome-keyring). A profile created on macOS will
**fail to decrypt cookies on the Linux VPS** → auth lost on transplant. Therefore,
for Approach B the interactive login must happen **on the VPS itself**:

- **Option B1 (recommended):** `xvfb-run` a headed Playwright Chromium on the VPS with
  `x11vnc` exposing the virtual display; tunnel VNC over SSH
  (`ssh -L 5900:localhost:5900 ...`) and complete the login from the local VNC client.
  Profile is created and consumed on the same OS — no transplant.
- **Option B2:** Pre-seed `linkedin_email`/`linkedin_password` and drive the existing
  `login()` automation on the VPS under xvfb; only attach VNC if a 2FA/captcha
  checkpoint trips. Lower setup, fails when challenges appear.

This OS-encryption hazard is the single biggest reason to prefer **Approach A** if
Phase 0 Test 1 passes.

## 7. Settings & files

- `data/linkedin_state.json` (Approach A) or `data/li_profile/` (Approach B) — **must be added to `.gitignore`** explicitly. `.gitignore` only lists specific `data/` paths (e.g. `data/linkedin_cookies.json`, `data/jobs/`), not the whole tree, so add `data/linkedin_state.json` / `data/li_profile/`. Both contain live session secrets — `chmod 600` / dir `700`.
- `.env`: `LINKEDIN_STORAGE_STATE_PATH` (A) or `LINKEDIN_USER_DATA_DIR` (B).
- Update the **`vps` skill** and **`production` skill** cookie-refresh sections to the new procedure once chosen.

## 8. Testing

- **Unit:** `initialize()` builds a persistent context when `user_data_dir` set, legacy context otherwise; `close()`/`is_alive()` handle `browser is None`.
- **Integration (manual, gated):** capture → deploy → trigger a search → assert the newest jobs have `session_authenticated=1` in prod DB and SDUI markers present.
- **Regression:** confirm descriptions still populate and no `InvalidStateTransition`/queue regressions.
- Run existing `pytest` + `pytest tests/e2e/test_hitl_review.py -m e2e` to ensure no breakage in the browser lifecycle consumers.

## 9. Rollout

1. Phase 0 experiment → pick A or B.
2. Implement on a branch; `black`/`mypy`; tests.
3. Deploy to VPS; perform the new login/capture; verify `linkedin_auth=Authenticated` on `GET /api/admin/queue` and per-job `session_authenticated=1`.
4. Update skills docs.

## 10. Rollback

The legacy cookie-jar path (`_load_cookies` + `add_cookies`) stays in the code behind
the absence of the new setting. Unset `LINKEDIN_STORAGE_STATE_PATH` /
`LINKEDIN_USER_DATA_DIR` to revert to today's behavior (guest scraping with full
descriptions) with no code change.

## 11. Open questions

- Phase 0 outcome (A vs B) — **blocks implementation choice**.
- Session longevity: how long does the authenticated session survive scraping before LinkedIn downgrades it again? Add an alert (the scheduler already has `maybe_alert_unauthenticated_session`) and measure.
- If Approach B + VNC is needed, is the operational overhead acceptable vs. accepting guest scraping until Easy Apply is built?
```
