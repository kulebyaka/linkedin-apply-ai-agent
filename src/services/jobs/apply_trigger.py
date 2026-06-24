"""Shared trigger for dispatching a deterministic Easy Apply run.

Used by every path that needs to start an application:
- the HITL **approve** decision (``HITLProcessor._handle_approve``),
- the ``auto_apply`` branch of the preparation workflow (``save_to_db_node``),
- the manual retry endpoint (``POST /api/jobs/{job_id}/apply``).

Fail-fast model (ARCHITECTURE-browser-agent.md §9): if no extension session is
connected for the user when an apply fires, the job is parked in the recoverable
``NEEDS_EXTENSION`` state rather than queued server-side — the user re-triggers
once the extension connects. When a session *is* present we set ``APPLYING`` and
dispatch the deterministic application workflow as a background task.

The caller is responsible for putting the job into a state from which
``APPLYING`` / ``NEEDS_EXTENSION`` are valid transitions (typically ``APPROVED``).
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from src.models.cv import ContactInfo
from src.models.state_machine import BusinessState

if TYPE_CHECKING:
    from src.context import AppContext

logger = logging.getLogger(__name__)

# Surfaced to the user (and stored on the job) when no extension is connected.
NEEDS_EXTENSION_MESSAGE = "Open the extension in your browser to apply."


def _contact_info_from_master_cv(master_cv_json: dict | None) -> ContactInfo | None:
    """Best-effort ``ContactInfo`` from the user's master CV (for form auto-fill).

    Incomplete/absent contact data is non-fatal: the classifier simply has fewer
    values to resolve from, and any required-but-missing field aborts to
    ``manual_required`` later — we never guess.
    """
    if not master_cv_json:
        return None
    contact = master_cv_json.get("contact")
    if not isinstance(contact, dict):
        return None
    try:
        return ContactInfo(**contact)
    except Exception:  # noqa: BLE001 — incomplete contact info is non-fatal
        logger.warning(
            "Could not parse contact info from master CV; continuing without it"
        )
        return None


async def trigger_apply(ctx: AppContext, job_id: str, user_id: str) -> BusinessState:
    """Dispatch an Easy Apply run for a job that's ready to apply.

    Returns the resulting ``BusinessState``: ``APPLYING`` when an extension
    session is connected (the application workflow is dispatched in the
    background), else ``NEEDS_EXTENSION`` (the job is parked for manual retry).
    """
    session_store = ctx.session_store
    connected = session_store is not None and await session_store.is_connected(user_id)

    if not connected:
        logger.info(
            "No extension session for user %s; parking job %s in needs_extension",
            user_id,
            job_id,
        )
        await ctx.repository.update(
            job_id,
            {
                "status": BusinessState.NEEDS_EXTENSION,
                "error_message": NEEDS_EXTENSION_MESSAGE,
            },
        )
        return BusinessState.NEEDS_EXTENSION

    job = await ctx.repository.get_for_user(job_id, user_id)
    if job is None:
        raise KeyError(f"Job {job_id} not found for user {user_id}")

    apply_profile = None
    contact_info = None
    if ctx.user_repository is not None:
        user = await ctx.user_repository.get_by_id(user_id)
        if user is not None:
            apply_profile = user.apply_profile
            contact_info = _contact_info_from_master_cv(user.master_cv_json)

    job_url = job.application_url or (job.job_posting or {}).get("url")
    initial_state = {
        "job_id": job_id,
        "user_id": user_id,
        "job_url": job_url,
        "pdf_path": job.current_pdf_path,
        "apply_profile": apply_profile,
        "contact_info": contact_info,
    }

    await ctx.repository.update(
        job_id, {"status": BusinessState.APPLYING, "error_message": None}
    )

    dispatcher = ctx.workflow_dispatcher
    if dispatcher is None:
        raise RuntimeError("workflow_dispatcher not initialized on AppContext")

    ctx.create_background_task(
        dispatcher.dispatch_application(
            job_id=job_id,
            thread_id=str(uuid.uuid4()),
            initial_state=initial_state,
            user_id=user_id,
        )
    )
    logger.info("Dispatched apply workflow for job %s (user %s)", job_id, user_id)
    return BusinessState.APPLYING
