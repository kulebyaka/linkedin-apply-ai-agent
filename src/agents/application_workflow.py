"""Application Workflow — deterministic (no-LLM) LinkedIn Easy Apply.

Re-created for the Easy Apply happy-path sprint (the prior stub was removed in
``9ced769``). This workflow drives the multi-step Easy Apply form deterministically
over the extension bridge (``ApplyBridge``) — there is **no LLM agent** this sprint.

Flow (mirrors ARCHITECTURE-browser-agent.md §4):

    open_easy_apply ──► fill_step ──(loop)──► fill_step
                              │                    │
                              ├──► submit ─────────┤
                              │                    │
                              └────────────────────┴──► finalize ──► END

``fill_step`` reads + classifies the current step. Any **unknown field** (or a
recognized field with no profile value), unfixable validation error, or the
LinkedIn daily-limit message aborts the run — we never guess a screening answer.
A dropped extension session maps to the recoverable ``NEEDS_EXTENSION`` state; a
per-application wall-clock budget and a max-step guard prevent runaway loops.

Terminal writes (respecting ``ALLOWED_TRANSITIONS``): ``APPLIED`` (+ application
URL + confirmation screenshot), ``MANUAL_REQUIRED`` (+ reason), ``NEEDS_EXTENSION``,
or ``FAILED`` (+ error). The repository write happens once, in ``finalize``.

NOTE (LLM sprint): when the agent lands, ``unknown_fields`` will route to an LLM
decision instead of straight to ``manual_required``; the bridge tool surface and
this state shape are intentionally unchanged so that's additive.

(No ``from __future__ import annotations`` here: LangGraph inspects the runtime
type of each node's ``config`` parameter to decide whether to inject the real
``RunnableConfig`` — stringized annotations defeat that and yield an empty config.)
"""

import base64
import logging
import time
from pathlib import Path
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from ..config.settings import Settings, get_settings
from ..models.cv import ContactInfo
from ..models.state_machine import ALLOWED_TRANSITIONS, BusinessState
from ..models.user import ApplyProfile
from ..services.linkedin.apply_bridge import ApplyBridge, ExtensionUnavailable
from ._shared import get_repository_from_config

logger = logging.getLogger(__name__)

# Max Easy Apply steps before we abort (AutoApplyMax content-simple.js :778).
MAX_STEPS = 10


class ApplyWorkflowState(TypedDict, total=False):
    """State for the deterministic Easy Apply workflow."""

    # Input
    job_id: str
    user_id: str
    job_url: str
    pdf_path: str
    apply_profile: ApplyProfile | None
    contact_info: ContactInfo | None

    # Internal control
    step_count: int
    deadline: float  # time.monotonic() budget
    route: str  # "fill" | "submit" | "finalize"

    # Outcome
    final_status: str
    manual_reason: str | None
    application_url: str | None
    confirmation_screenshot_path: str | None
    confirmation_text: str
    error_message: str | None
    current_step: str


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def get_apply_bridge_from_config(config: dict) -> ApplyBridge:
    """Extract the ApplyBridge from ``config['configurable']``."""
    bridge = (config or {}).get("configurable", {}).get("apply_bridge")
    if bridge is None:
        raise RuntimeError(
            "ApplyBridge not found in workflow config. Pass it via "
            "config={'configurable': {'apply_bridge': bridge}}"
        )
    return bridge


def get_settings_from_config(config: dict) -> Settings:
    """Extract Settings from config, falling back to the global settings."""
    settings = (config or {}).get("configurable", {}).get("settings")
    return settings or get_settings()


# ---------------------------------------------------------------------------
# Outcome helpers
# ---------------------------------------------------------------------------


def _terminal(
    state: ApplyWorkflowState,
    status: BusinessState,
    *,
    error: str | None = None,
    manual_reason: str | None = None,
) -> ApplyWorkflowState:
    """Record a terminal outcome and route the graph to ``finalize``."""
    state["final_status"] = str(status)
    state["route"] = "finalize"
    if error is not None:
        state["error_message"] = error
    if manual_reason is not None:
        state["manual_reason"] = manual_reason
    return state


async def _safe_discard(bridge: ApplyBridge, user_id: str, reason: str) -> None:
    """Best-effort discard; never raise out of the abort path."""
    try:
        await bridge.discard(user_id, reason)
    except Exception:  # noqa: BLE001 — discard is best-effort cleanup
        logger.warning("Discard failed for user %s during abort (%s)", user_id, reason)


def _timed_out(state: ApplyWorkflowState) -> bool:
    deadline = state.get("deadline")
    return deadline is not None and time.monotonic() >= deadline


# ---------------------------------------------------------------------------
# Workflow construction
# ---------------------------------------------------------------------------


def create_application_workflow() -> StateGraph:
    """Build and compile the deterministic Easy Apply workflow."""
    workflow = StateGraph(ApplyWorkflowState)

    workflow.add_node("open_easy_apply", open_easy_apply_node)
    workflow.add_node("fill_step", fill_step_node)
    workflow.add_node("submit", submit_node)
    workflow.add_node("finalize", finalize_node)

    workflow.set_entry_point("open_easy_apply")
    workflow.add_conditional_edges(
        "open_easy_apply",
        _route,
        {"fill": "fill_step", "finalize": "finalize"},
    )
    workflow.add_conditional_edges(
        "fill_step",
        _route,
        {"fill": "fill_step", "submit": "submit", "finalize": "finalize"},
    )
    workflow.add_edge("submit", "finalize")
    workflow.add_edge("finalize", END)

    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)


def _route(state: ApplyWorkflowState) -> str:
    """Conditional-edge router: read the route the last node decided."""
    return state.get("route", "finalize")


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def open_easy_apply_node(
    state: ApplyWorkflowState, config: RunnableConfig | None = None
) -> ApplyWorkflowState:
    """Open the Easy Apply modal; arm the per-application wall-clock budget."""
    cfg = dict(config or {})
    bridge = get_apply_bridge_from_config(cfg)
    settings = get_settings_from_config(cfg)
    user_id = state.get("user_id", "")

    state["step_count"] = 0
    state["deadline"] = time.monotonic() + settings.apply_per_app_timeout_seconds
    state["current_step"] = "open_easy_apply"

    try:
        opened = await bridge.open_easy_apply(user_id, state.get("job_url"))
    except ExtensionUnavailable as exc:
        logger.info("Extension unavailable while opening apply for %s: %s", user_id, exc)
        return _terminal(state, BusinessState.NEEDS_EXTENSION, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to open Easy Apply for %s: %s", user_id, exc, exc_info=True)
        return _terminal(state, BusinessState.FAILED, error=f"open_easy_apply failed: {exc}")

    if not opened:
        return _terminal(
            state, BusinessState.FAILED, error="Easy Apply modal did not open"
        )

    state["route"] = "fill"
    return state


async def fill_step_node(
    state: ApplyWorkflowState, config: RunnableConfig | None = None
) -> ApplyWorkflowState:
    """Read + classify one step, fill known fields, then advance or submit."""
    cfg = dict(config or {})
    bridge = get_apply_bridge_from_config(cfg)
    user_id = state.get("user_id", "")
    state["current_step"] = "fill_step"

    # Wall-clock budget guard.
    if _timed_out(state):
        await _safe_discard(bridge, user_id, "per-application timeout")
        return _terminal(
            state, BusinessState.FAILED, error="Application timed out before completion"
        )

    # Max-step guard (runaway loop protection).
    state["step_count"] = state.get("step_count", 0) + 1
    if state["step_count"] > MAX_STEPS:
        await _safe_discard(bridge, user_id, "exceeded max steps")
        return _terminal(
            state, BusinessState.FAILED, error=f"Exceeded {MAX_STEPS} Easy Apply steps"
        )

    try:
        form = await bridge.read_form_state(
            user_id, state.get("apply_profile"), state.get("contact_info")
        )
    except ExtensionUnavailable as exc:
        return _terminal(state, BusinessState.NEEDS_EXTENSION, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error("read_form_state failed for %s: %s", user_id, exc, exc_info=True)
        return _terminal(state, BusinessState.FAILED, error=f"read_form_state failed: {exc}")

    # LinkedIn daily Easy Apply limit — stop and record, do NOT retry.
    if form.daily_limit_reached:
        await _safe_discard(bridge, user_id, "daily limit reached")
        return _terminal(
            state,
            BusinessState.FAILED,
            error="LinkedIn daily Easy Apply limit reached",
        )

    # Unknown / missing-value field — never guess. Abort to manual_required.
    if form.unknown_fields:
        await _safe_discard(bridge, user_id, "unknown fields")
        labels = "; ".join(u.label or u.reason for u in form.unknown_fields)
        return _terminal(
            state,
            BusinessState.MANUAL_REQUIRED,
            manual_reason=f"Unrecognized or unanswerable fields: {labels}",
        )

    try:
        # Upload the tailored resume into any file input on this step.
        pdf_path = state.get("pdf_path")
        for skip in form.skipped:
            if "upload_file" in skip.reason and pdf_path:
                await bridge.upload_file(user_id, skip.selector, pdf_path)

        # Fill every resolved field.
        for fill in form.fill_plan:
            await bridge.fill_field(user_id, fill.selector, fill.value)

        advance = await bridge.advance_step(user_id)
    except ExtensionUnavailable as exc:
        return _terminal(state, BusinessState.NEEDS_EXTENSION, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error("fill/advance failed for %s: %s", user_id, exc, exc_info=True)
        return _terminal(state, BusinessState.FAILED, error=f"fill_step failed: {exc}")

    # Validation errors we can't auto-correct — abort to manual.
    if advance.errors:
        await _safe_discard(bridge, user_id, "validation errors")
        return _terminal(
            state,
            BusinessState.MANUAL_REQUIRED,
            manual_reason="Validation errors: " + "; ".join(advance.errors),
        )

    if advance.advanced:
        state["route"] = "fill"  # More steps — loop.
        return state

    # No Next/Review button advanced us → we're on the final (submit) step.
    state["route"] = "submit"
    return state


async def submit_node(
    state: ApplyWorkflowState, config: RunnableConfig | None = None
) -> ApplyWorkflowState:
    """Submit the application and capture the confirmation screenshot."""
    cfg = dict(config or {})
    bridge = get_apply_bridge_from_config(cfg)
    settings = get_settings_from_config(cfg)
    user_id = state.get("user_id", "")
    state["current_step"] = "submit"

    if _timed_out(state):
        await _safe_discard(bridge, user_id, "per-application timeout")
        return _terminal(
            state, BusinessState.FAILED, error="Application timed out before submit"
        )

    try:
        result = await bridge.submit_form(user_id)
    except ExtensionUnavailable as exc:
        return _terminal(state, BusinessState.NEEDS_EXTENSION, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error("submit_form failed for %s: %s", user_id, exc, exc_info=True)
        return _terminal(state, BusinessState.FAILED, error=f"submit_form failed: {exc}")

    if not result.confirmed:
        return _terminal(
            state, BusinessState.FAILED, error="Submission was not confirmed"
        )

    state["application_url"] = state.get("job_url")
    state["confirmation_text"] = result.confirmation_text
    state["confirmation_screenshot_path"] = _save_screenshot(
        settings, user_id, state.get("job_id", ""), result.screenshot_b64
    )
    return _terminal(state, BusinessState.APPLIED)


async def finalize_node(
    state: ApplyWorkflowState, config: RunnableConfig | None = None
) -> ApplyWorkflowState:
    """Persist the terminal outcome, respecting ``ALLOWED_TRANSITIONS``."""
    cfg = dict(config or {})
    repo = get_repository_from_config(cfg)
    job_id = state.get("job_id", "")
    target = BusinessState(state.get("final_status") or BusinessState.FAILED)

    updates: dict = {"status": target}
    if target == BusinessState.APPLIED:
        if state.get("application_url"):
            updates["application_url"] = state["application_url"]
        updates["error_message"] = None
    elif target == BusinessState.MANUAL_REQUIRED:
        updates["error_message"] = state.get("manual_reason") or "Manual application required"
    elif state.get("error_message"):
        updates["error_message"] = state["error_message"]

    try:
        existing = await repo.get(job_id)
        if existing is None:
            logger.warning("finalize: job %s not found; cannot persist %s", job_id, target)
            state["current_step"] = str(target)
            return state

        allowed = ALLOWED_TRANSITIONS.get(existing.status, set())
        if existing.status != target and target not in allowed:
            logger.warning(
                "finalize: illegal transition %s → %s for job %s; leaving status unchanged",
                existing.status, target, job_id,
            )
            state["current_step"] = str(existing.status)
            return state

        await repo.update(job_id, updates)
        logger.info("Apply workflow for job %s finished: %s", job_id, target)
    except Exception as exc:  # noqa: BLE001
        logger.error("finalize failed for job %s: %s", job_id, exc, exc_info=True)
        state["error_message"] = f"Failed to persist apply result: {exc}"

    state["current_step"] = str(target)
    return state


# ---------------------------------------------------------------------------
# Confirmation screenshot
# ---------------------------------------------------------------------------


def _save_screenshot(
    settings: Settings, user_id: str, job_id: str, screenshot_b64: str | None
) -> str | None:
    """Persist the confirmation screenshot next to the user's generated CVs.

    Best-effort: a capture failure must never turn a confirmed application into
    a failure. Returns the file path on success, else ``None``.
    """
    if not screenshot_b64:
        return None
    raw = screenshot_b64.split(",", 1)[-1] if "," in screenshot_b64 else screenshot_b64
    try:
        data = base64.b64decode(raw)
        out_dir = Path(settings.generated_cvs_dir) / user_id
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{job_id}_confirmation.png"
        path.write_bytes(data)
        return str(path)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to persist confirmation screenshot for job %s", job_id)
        return None
