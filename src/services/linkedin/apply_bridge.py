"""Deterministic Easy Apply bridge tools (no-LLM sprint).

``ApplyBridge`` is the server-side tool surface that drives a LinkedIn Easy Apply
form one RPC at a time over the extension WebSocket bridge (``WsRelay``). The
content script (``extension/content_script.js``) is a dumb DOM actuator; this
module owns the *decisions*: it serializes the form, classifies every field via
``field_classifier`` (never guessing — unknowns abort to ``manual_required``),
fills resolved values, advances steps, uploads the tailored PDF, and submits.

The method signatures here are intentionally the surface a future LLM sprint can
wrap with ``create_sdk_mcp_server`` / ``@tool`` **without changing the WS
protocol** — each method is a plain ``async def`` keyed on ``user_id`` plus
JSON-serializable args/returns. (YAGNI: no MCP layer until there's an agent.)

NOTE (LLM sprint): unmatched fields currently surface as ``unknown_fields`` and
the workflow aborts; the agent will instead route them to an LLM decision. PII /
placeholder substitution is also deferred — with no untrusted LLM context to
protect, the server sends real profile values straight to ``fill_field``.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from src.bridge import BridgeDisconnected, BridgeTimeout
from src.services.linkedin.easy_apply_selectors import (
    CONFIRMATION_PATTERNS,
    DAILY_LIMIT_PATTERNS,
)
from src.services.linkedin.field_classifier import (
    FieldFill,
    SerializedField,
    Skip,
    Unknown,
    classify_field,
)

if TYPE_CHECKING:
    from src.bridge import WsRelay
    from src.config.settings import Settings
    from src.models.cv import ContactInfo
    from src.models.user import ApplyProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ApplyBridgeError(Exception):
    """A bridge RPC reported an error result (e.g. element not found)."""


class ExtensionUnavailable(Exception):  # noqa: N818 — name is part of the tool contract
    """The extension session dropped mid-apply.

    The apply workflow maps this to the recoverable ``NEEDS_EXTENSION`` state
    (vs. ``FAILED`` for other errors). Translated from ``BridgeDisconnected`` and
    ``BridgeTimeout`` so callers never have to know about the transport layer.
    """


# ---------------------------------------------------------------------------
# I/O models
# ---------------------------------------------------------------------------


class FormState(BaseModel):
    """Classified snapshot of the current Easy Apply step.

    ``fill_plan`` holds the actionable text/choice/checkbox fills; ``skipped``
    are intentionally-ignored fields (file inputs, follow-company); and
    ``unknown_fields`` is the abort signal — any entry here means the workflow
    discards and marks ``manual_required`` (we never guess a screening answer).
    """

    step: int | None = None
    total: int | None = None
    fields: list[SerializedField] = Field(default_factory=list)
    fill_plan: list[FieldFill] = Field(default_factory=list)
    skipped: list[Skip] = Field(default_factory=list)
    unknown_fields: list[Unknown] = Field(default_factory=list)
    daily_limit_reached: bool = False
    has_spinner: bool = False
    modal_present: bool = False
    page_text_excerpt: str = ""


class AdvanceResult(BaseModel):
    """Outcome of clicking Next/Review and re-reading the form."""

    advanced: bool = False
    errors: list[str] = Field(default_factory=list)


class SubmitResult(BaseModel):
    """Outcome of the final un-follow → submit → done sequence."""

    confirmed: bool = False
    screenshot_b64: str | None = None
    confirmation_text: str = ""


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


class ApplyBridge:
    """Deterministic per-field Easy Apply tools over the WebSocket bridge."""

    def __init__(self, relay: WsRelay, settings: Settings) -> None:
        self._relay = relay
        self._settings = settings

    # ------------------------------------------------------------------ #
    # Tools
    # ------------------------------------------------------------------ #
    async def read_form_state(
        self,
        user_id: str,
        apply_profile: ApplyProfile | None = None,
        contact_info: ContactInfo | None = None,
    ) -> FormState:
        """Serialize the current step and classify every field.

        Runs ``field_classifier`` over each serialized field, attaching the
        per-field fill plan and surfacing ``unknown_fields`` (unrecognized OR
        recognized-but-missing-value) so the caller can abort. Also scans the
        page-text excerpt for the LinkedIn daily Easy Apply limit message.
        """
        raw = await self._rpc_result(user_id, "serialize_form")

        fields = [SerializedField(**f) for f in raw.get("fields") or []]
        fill_plan: list[FieldFill] = []
        skipped: list[Skip] = []
        unknown: list[Unknown] = []
        for field in fields:
            outcome = classify_field(field, apply_profile, contact_info)
            if isinstance(outcome, FieldFill):
                fill_plan.append(outcome)
            elif isinstance(outcome, Skip):
                skipped.append(outcome)
            else:
                unknown.append(outcome)

        flags = raw.get("flags") or {}
        excerpt = flags.get("page_text_excerpt") or ""
        return FormState(
            step=raw.get("step"),
            total=raw.get("total"),
            fields=fields,
            fill_plan=fill_plan,
            skipped=skipped,
            unknown_fields=unknown,
            daily_limit_reached=self._detect_daily_limit(excerpt),
            has_spinner=bool(flags.get("has_spinner")),
            modal_present=bool(flags.get("modal_present")),
            page_text_excerpt=excerpt,
        )

    async def fill_field(self, user_id: str, selector: str, value: str) -> dict:
        """Write ``value`` into ``selector``; raise on a content-script error."""
        frame = await self._rpc(user_id, "fill_field", {"selector": selector, "value": value})
        result, error = self._unwrap(frame)
        if error is not None:
            raise ApplyBridgeError(f"fill_field failed for {selector!r}: {error}")
        return result

    async def advance_step(self, user_id: str) -> AdvanceResult:
        """Click Next (falling back to Review), then scan for validation errors.

        ``advanced`` is True only when a navigation button was clicked AND the
        re-read form surfaces no inline errors. The caller decides what to do
        with ``errors`` (typically discard → ``manual_required`` when it can't
        be auto-corrected).
        """
        clicked = await self._click_advance(user_id)
        state_raw = await self._rpc_result(user_id, "serialize_form")
        errors = self._scan_errors(state_raw)
        return AdvanceResult(advanced=clicked and not errors, errors=errors)

    async def upload_file(self, user_id: str, selector: str, pdf_path: str) -> dict:
        """Base64-encode the tailored PDF and push it into the file input.

        ``pdf_path`` is the per-user generated CV (``data/generated_cvs/
        {user_id}/{job_id}.pdf``); the content script reconstructs a ``File`` via
        ``DataTransfer``.
        """
        path = Path(pdf_path)
        data = path.read_bytes()
        data_url = "data:application/pdf;base64," + base64.b64encode(data).decode("ascii")
        frame = await self._rpc(
            user_id,
            "upload_file",
            {
                "selector": selector,
                "dataUrl": data_url,
                "filename": path.name,
                "mime": "application/pdf",
            },
        )
        result, error = self._unwrap(frame)
        if error is not None:
            raise ApplyBridgeError(f"upload_file failed for {path.name}: {error}")
        return result

    async def submit_form(self, user_id: str) -> SubmitResult:
        """Un-follow the company, submit, click Done, and capture confirmation.

        Ordering matters: the follow-company checkbox is un-ticked *before* the
        Submit click so we never silently follow employers (AutoApplyMax
        :1377-1408). Confirmation is a best-effort screenshot + modal text grab.
        """
        # The whole submit sequence runs under a cleanup wrapper: any RPC below
        # can raise ExtensionUnavailable (a BridgeTimeout after the content
        # script already processed the call, or a mid-submit drop). The mutation
        # gate may be open, so end_session() must be attempted on every exit
        # path — finally guarantees that without masking the original error
        # (end_session is best-effort and never raises).
        #
        # Defaults stand in for an unconfirmed submission: if transport is lost
        # at/after the submit click we leave these unset, so the confirmation
        # check below evaluates to confirmed=False and the workflow routes to
        # terminal FAILED rather than retryable NEEDS_EXTENSION.
        submit_result: dict | None = None
        submit_error: str | None = None
        capture: dict | None = None
        shot: dict | None = None
        try:
            # 1. Un-follow before submit (best-effort). This is PRE-submit — a
            #    disconnect here legitimately means nothing was submitted, so we
            #    let ExtensionUnavailable propagate to the recoverable
            #    NEEDS_EXTENSION (no risk of a duplicate apply).
            await self._rpc_best_effort(user_id, "unfollow_company")
            # 2. Submit and capture the confirmation. Everything from the submit
            #    click onward is wrapped: once the Submit RPC has been *attempted*
            #    LinkedIn may already have accepted the application (a BridgeTimeout
            #    can fire after the content script clicked Submit), so a transport
            #    loss in this window must NOT surface as the recoverable
            #    ExtensionUnavailable — submit_node() would map that to
            #    NEEDS_EXTENSION and a user re-trigger could duplicate an
            #    already-sent application. Swallow the drop and fall through to the
            #    confirmation check, which (lacking captured confirmation text)
            #    yields confirmed=False → terminal FAILED, not retryable.
            try:
                submit_frame = await self._rpc(user_id, "click_button", {"role": "submit"})
                submit_result, submit_error = self._unwrap(submit_frame)
                # Capture the confirmation BEFORE dismissing it: clicking Done
                # closes the "Application sent" modal, so the screenshot/text must
                # be grabbed while it's still on screen.
                capture = await self._rpc_best_effort(user_id, "capture_visible")
                shot = await self._rpc_best_effort(user_id, "take_screenshot")
                # Final Done / confirmation control — pure cleanup that only
                # dismisses the "Application sent" modal after capture.
                await self._rpc_best_effort(user_id, "find_and_click_done")
            except ExtensionUnavailable:
                pass
        finally:
            await self.end_session(user_id)
        confirmation_text = (shot or {}).get("confirmation_text", "")
        # A Submit-button click alone is NOT proof the application was accepted
        # (LinkedIn may re-show the form with a validation error). Require the
        # post-submit "Application sent" modal text before declaring success;
        # otherwise the apply workflow correctly routes to FAILED rather than
        # falsely marking the job APPLIED.
        clicked = submit_error is None and bool((submit_result or {}).get("clicked"))
        confirmed = clicked and self._detect_confirmation(confirmation_text)
        return SubmitResult(
            confirmed=confirmed,
            screenshot_b64=(capture or {}).get("screenshot_b64"),
            confirmation_text=confirmation_text,
        )

    async def discard(self, user_id: str, reason: str = "") -> dict:
        """Discard the in-progress application (X → confirm → ESC → scan)."""
        logger.info("Discarding Easy Apply for user %s: %s", user_id, reason or "(no reason)")
        # discard_application can raise ExtensionUnavailable (a BridgeTimeout
        # after the content script already discarded, or a mid-discard drop).
        # The mutation gate may be open, so end_session() must be attempted even
        # on that path — finally guarantees it without masking the original error
        # (end_session is best-effort and never raises).
        try:
            result = await self._rpc_best_effort(user_id, "discard_application")
        finally:
            await self.end_session(user_id)
        return result

    async def open_easy_apply(self, user_id: str, job_url: str | None = None) -> bool:
        """Navigate to the job (if given) and click Easy Apply.

        Opens the mutation gate (``begin_session``) before any mutating
        primitive — the content script blocks ``fill_field``/``click_button``/etc.
        until the server explicitly begins a session (security model, see
        ``content_script.js`` header). The content script handles the LinkedIn
        safety-reminder dialog ("Continue applying", AutoApplyMax :665-687) and
        reports whether the Easy Apply modal became visible. Returns True only
        when the modal actually opened so the workflow can fail fast otherwise.

        ``navigate`` is a **hard** step (``_rpc_result``): the extension refuses
        to navigate the tab to a non-LinkedIn URL, and swallowing that refusal
        would proceed to click Easy Apply on whatever job is already open. A
        rejected navigation raises ``ApplyBridgeError`` so the workflow aborts
        rather than applying against the wrong posting.
        """
        try:
            # begin_session is inside the cleanup wrapper: the extension sets
            # sessionActive=true / opens the content-script gate the moment it
            # *processes* the RPC (background.js routeRpc), so a lost reply or a
            # timeout on this very call (ExtensionUnavailable) can leave the gate
            # armed. Closing it here covers that path too.
            await self._rpc_best_effort(user_id, "begin_session")
            if job_url:
                await self._rpc_result(user_id, "navigate", {"url": job_url})
            result = await self._rpc_result(user_id, "open_easy_apply")
            opened = bool(result.get("opened"))
        except Exception:
            # begin_session/navigate/open raised (rejected navigation,
            # content-script error, or a dropped/timed-out session). Close the
            # mutation gate we may have opened so the actuator is not left armed
            # beyond this aborted apply, then re-raise for the workflow to route.
            # end_session is best-effort (never raises, swallows
            # ExtensionUnavailable).
            await self.end_session(user_id)
            raise
        if not opened:
            # Modal never appeared — same cleanup so the gate does not stay open.
            await self.end_session(user_id)
        return opened

    async def end_session(self, user_id: str) -> dict:
        """Close the mutation gate at the end of an apply run.

        Pure cleanup — must never raise (a dropped extension after a confirmed
        submit must not turn ``APPLIED`` into ``NEEDS_EXTENSION``).
        """
        try:
            return await self._rpc_best_effort(user_id, "end_session")
        except ExtensionUnavailable:
            return {}

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    async def _click_advance(self, user_id: str) -> bool:
        """Click the Next button, falling back to Review."""
        for role in ("next", "review"):
            frame = await self._rpc(user_id, "click_button", {"role": role})
            result, error = self._unwrap(frame)
            if error is None and result.get("clicked"):
                return True
        return False

    def _scan_errors(self, state_raw: dict) -> list[str]:
        """Extract inline validation error strings from a serialized form.

        The content script surfaces ``flags.errors`` when the page shows
        ``[role=alert]`` / inline feedback (AutoApplyMax :800-825, :1436-1477);
        absent that key (current actuator build) this is empty and the caller
        relies on ``advanced`` instead.
        """
        flags = state_raw.get("flags") or {}
        raw_errors = flags.get("errors") or []
        out: list[str] = []
        for item in raw_errors:
            if isinstance(item, dict):
                text = item.get("text") or item.get("message") or ""
            else:
                text = str(item)
            text = text.strip()
            if text:
                out.append(text)
        return out

    def _detect_daily_limit(self, page_text: str) -> bool:
        if not self._settings.apply_daily_limit_detection:
            return False
        low = page_text.lower()
        return any(pattern.lower() in low for pattern in DAILY_LIMIT_PATTERNS)

    @staticmethod
    def _detect_confirmation(modal_text: str) -> bool:
        """True when the post-submit modal text matches an 'Application sent' phrase."""
        if not modal_text:
            return False
        low = modal_text.lower()
        return any(pattern in low for pattern in CONFIRMATION_PATTERNS)

    async def _rpc(self, user_id: str, method: str, params: dict | None = None) -> dict:
        """Send one RPC, translating a dropped session into a typed error."""
        try:
            return await self._relay.send_rpc(
                user_id,
                method,
                params or {},
                timeout=self._settings.apply_rpc_timeout_seconds,
            )
        except (BridgeDisconnected, BridgeTimeout) as exc:
            # A dropped session (BridgeDisconnected) or a stalled/unanswered RPC
            # (BridgeTimeout — e.g. a silent network drop or a session displaced
            # mid-apply) both mean the extension can no longer drive this apply.
            # Surface as the recoverable ExtensionUnavailable so the workflow
            # routes to NEEDS_EXTENSION instead of a hard FAILED.
            raise ExtensionUnavailable(str(exc)) from exc

    async def _rpc_result(self, user_id: str, method: str, params: dict | None = None) -> dict:
        """Send an RPC and return its result dict, raising on a reported error."""
        frame = await self._rpc(user_id, method, params)
        result, error = self._unwrap(frame)
        if error is not None:
            raise ApplyBridgeError(f"{method} failed: {error}")
        return result

    async def _rpc_best_effort(self, user_id: str, method: str, params: dict | None = None) -> dict:
        """Send an RPC ignoring content-script errors (still surfaces disconnect)."""
        frame = await self._rpc(user_id, method, params)
        result, _error = self._unwrap(frame)
        return result

    @staticmethod
    def _unwrap(frame: dict) -> tuple[dict, str | None]:
        """Split a reply frame into (result_dict, error_or_None).

        The relay returns the whole reply frame; background.js promotes a
        content-script ``{error}`` to a frame-level ``error`` key, but we also
        defend against a nested one.
        """
        if not isinstance(frame, dict):
            return {}, "malformed reply frame"
        if frame.get("error"):
            return {}, str(frame["error"])
        result = frame.get("result")
        if isinstance(result, dict) and result.get("error"):
            return {}, str(result["error"])
        return (result if isinstance(result, dict) else {}), None
