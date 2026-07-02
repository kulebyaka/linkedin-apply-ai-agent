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
from urllib.parse import urlparse

from src.models.cv import ContactInfo
from src.models.state_machine import BusinessState

if TYPE_CHECKING:
    from src.context import AppContext
    from src.models.unified import JobRecord

logger = logging.getLogger(__name__)

# Surfaced to the user (and stored on the job) when no extension is connected.
NEEDS_EXTENSION_MESSAGE = "Open the extension in your browser to apply."

# Surfaced when an apply is triggered for a job that isn't a LinkedIn Easy Apply
# posting (manual/URL sources, or a LinkedIn record with a non-LinkedIn URL).
# The automation only drives LinkedIn Easy Apply — anything else is finished by
# hand, so we park the job in MANUAL_REQUIRED rather than navigating the user's
# LinkedIn tab to an arbitrary URL (or applying against whatever job is open).
NON_LINKEDIN_MESSAGE = (
    "This job isn't a LinkedIn Easy Apply posting. Download your tailored CV "
    "and apply on the company site."
)


def _is_linkedin_url(url: str | None) -> bool:
    """Whether ``url`` is an ``https`` LinkedIn URL (mirrors ``isLinkedInUrl``).

    Authoritative host check, kept in sync with ``extension/background.js`` so
    the server never dispatches an apply whose navigation the extension would
    reject (and whose rejection ``open_easy_apply`` would silently swallow).
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "linkedin.com" or host.endswith(".linkedin.com"))


def _scraped_easy_apply_flag(job: JobRecord) -> bool | None:
    """The scraper's Easy Apply badge result for this job, if it was recorded.

    LinkedIn search cards carry an "Easy Apply" badge; the scraper records it as
    ``easy_apply`` in the persisted ``job_posting`` (under ``raw_data``). Returns
    ``None`` when the flag isn't present (older records / non-scraper paths) so a
    missing flag stays distinct from an explicit ``False``.
    """
    posting = job.job_posting if isinstance(job.job_posting, dict) else {}
    raw = posting.get("raw_data")
    if isinstance(raw, dict) and "easy_apply" in raw:
        return bool(raw["easy_apply"])
    if "easy_apply" in posting:
        return bool(posting["easy_apply"])
    return None


def _is_linkedin_easy_apply(job: JobRecord, job_url: str | None) -> bool:
    """Whether ``job`` is drivable by the LinkedIn Easy Apply automation.

    Requires a ``linkedin`` source (the authoritative signal that the job came
    from the LinkedIn scraper) *and* a LinkedIn target URL: the apply workflow
    navigates the active LinkedIn tab to ``job_url`` then clicks Easy Apply, so a
    missing URL would apply against whatever job happens to be open, and a
    non-LinkedIn URL can't be driven at all. The host is validated here (not just
    extension-side) because ``ApplyBridge.open_easy_apply`` sends ``navigate``
    best-effort and discards the extension's refusal — relying on that alone
    would let a non-LinkedIn URL fall through and apply against the open tab.

    A posting the scraper explicitly flagged as *not* Easy Apply (the card had no
    badge) can't be driven either — the modal never opens and the workflow would
    fail at "Easy Apply modal did not open". We treat that like any other
    non-Easy-Apply job and park it in MANUAL_REQUIRED up front (CV still
    downloadable). A *missing* flag is treated as "unknown" and still attempted,
    so a single missed badge can't silently skip a genuine Easy Apply job.
    """
    if job.source != "linkedin" or not _is_linkedin_url(job_url):
        return False
    return _scraped_easy_apply_flag(job) is not False


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
        logger.warning("Could not parse contact info from master CV; continuing without it")
        return None


async def trigger_apply(ctx: AppContext, job_id: str, user_id: str) -> BusinessState:
    """Dispatch an Easy Apply run for a job that's ready to apply.

    Returns the resulting ``BusinessState``: ``APPLYING`` when an extension
    session is connected (the application workflow is dispatched in the
    background), ``NEEDS_EXTENSION`` (parked for manual retry when no session is
    connected), or ``MANUAL_REQUIRED`` (the job isn't a LinkedIn Easy Apply
    posting, so the automation can't drive it).
    """
    job = await ctx.repository.get_for_user(job_id, user_id)
    if job is None:
        raise KeyError(f"Job {job_id} not found for user {user_id}")

    job_url = job.application_url or (job.job_posting or {}).get("url")

    # Only LinkedIn Easy Apply postings are drivable by the automation. Anything
    # else (manual/URL jobs, or a LinkedIn record without a LinkedIn URL) is
    # parked in MANUAL_REQUIRED — never dispatched — so we don't navigate the
    # user's LinkedIn tab to an arbitrary URL or apply against the wrong job.
    if not _is_linkedin_easy_apply(job, job_url):
        logger.info(
            "Job %s (source=%s, url=%s) is not LinkedIn Easy Apply; marking manual_required",
            job_id,
            job.source,
            job_url,
        )
        await ctx.repository.update(
            job_id,
            {
                "status": BusinessState.MANUAL_REQUIRED,
                "error_message": NON_LINKEDIN_MESSAGE,
            },
        )
        return BusinessState.MANUAL_REQUIRED

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

    # Atomically claim the job for application before doing any dispatch. The
    # claim flips a claimable status (APPROVED / NEEDS_EXTENSION / MANUAL_REQUIRED)
    # to APPLYING in a single conditional write, so two concurrent apply requests for the same
    # job coalesce: only the winner gets the record back, the loser sees None and
    # returns APPLYING without enqueueing a duplicate workflow.
    claimed = await ctx.repository.try_claim_for_apply(job_id)
    if claimed is None:
        logger.info(
            "Job %s already claimed for apply (concurrent request); not re-dispatching",
            job_id,
        )
        return BusinessState.APPLYING

    apply_profile = None
    contact_info = None
    if ctx.user_repository is not None:
        user = await ctx.user_repository.get_by_id(user_id)
        if user is not None:
            apply_profile = user.apply_profile
            contact_info = _contact_info_from_master_cv(user.master_cv_json)

    initial_state = {
        "job_id": job_id,
        "user_id": user_id,
        "job_url": job_url,
        "pdf_path": job.current_pdf_path,
        "apply_profile": apply_profile,
        "contact_info": contact_info,
    }

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
