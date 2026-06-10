"""Auto-refinement cycle: turn decline/override signals into a filter proposal.

A single LLM call per qualifying user, run off the request path by the
RefinementScheduler. Generates a PROPOSED update to the auto-learned criteria
block of the user's ``custom_prompt`` (never applied automatically) and emits a
persistent notification so the user can review it in Settings.

Lifecycle: signals on the job record move pending -> proposed (here) ->
consumed (on accept/reject). Each signal feeds the refiner exactly once.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.models.job_filter import RefinementProposal, extract_learned_block
from src.models.user import User

if TYPE_CHECKING:
    from src.context import AppContext

logger = logging.getLogger(__name__)

NOTIFICATION_TYPE = "filter_refinement"
FILTER_SETTINGS_URL = "/settings#filter"


def _signal_line(job) -> str:
    """Render a job's context for the refiner prompt."""
    posting = job.job_posting or {}
    title = posting.get("title") or "Unknown role"
    company = posting.get("company") or "unknown company"
    return f"{title} at {company}"


async def run_refinement_cycle(ctx: AppContext, user: User) -> RefinementProposal | None:
    """Run one refinement cycle for a single user.

    Returns the created proposal, or None when skipped (opt-out, gate not met,
    or LLM failure — signals are left ``pending`` to retry next cycle).
    Never raises: failures are logged and swallowed so the scheduler keeps going.
    """
    prefs = user.filter_preferences
    if prefs is None or not prefs.auto_refine_enabled:
        logger.debug("Refinement skipped for user %s: not opted in", user.id)
        return None

    # Don't stack proposals: if the user already has an un-acknowledged proposal,
    # leave it for them to review. A new cycle only begins once the prior one is
    # accepted/rejected. This prevents superseding an existing proposal (which
    # would orphan its already-"proposed" signals so their feedback is lost) and
    # prevents a re-feed / duplicate-notification loop if a prior cycle failed to
    # mark its signals "proposed".
    try:
        existing_proposal = await ctx.user_repository.get_pending_proposal(user.id)
    except Exception:
        logger.exception(
            "Refinement: failed to check existing proposal for user %s", user.id
        )
        return None
    if existing_proposal is not None:
        logger.info(
            "Refinement skipped for user %s: a proposal is awaiting review", user.id
        )
        return None

    settings = ctx.settings
    cap = settings.auto_refine_signal_cap
    min_signals = settings.auto_refine_min_signals

    try:
        signals = await ctx.repository.list_refine_signals(
            user.id, state="pending", limit=cap
        )
    except Exception:
        logger.exception("Refinement: failed to load signals for user %s", user.id)
        return None

    if len(signals) < min_signals:
        logger.info(
            "Refinement gate not met for user %s: %d/%d signals",
            user.id,
            len(signals),
            min_signals,
        )
        return None

    decline_signals: list[str] = []
    override_signals: list[str] = []
    for job in signals:
        ctx_line = _signal_line(job)
        # A job contributes to at most one list. The HITLProcessor decline guard
        # already prevents a forced-through (override) job from also becoming a
        # decline signal; the elif defends against any legacy row carrying both.
        if job.decline_reason:
            decline_signals.append(f"{ctx_line}: {job.decline_reason}")
        elif job.override_reason:
            override_signals.append(f"{ctx_line}: {job.override_reason}")

    current_block = extract_learned_block(prefs.custom_prompt) or ""

    # Resolve the LLM the same way the generate-prompt endpoint does.
    provider_override = None
    model_override = None
    if user.model_preferences and user.model_preferences.filter_prompt_generation:
        choice = user.model_preferences.filter_prompt_generation
        provider_override = choice.provider
        model_override = choice.model

    try:
        from src.agents._shared import create_llm_client
        from src.services.jobs.job_filter import JobFilter

        llm_client = create_llm_client(provider_override, model_override)
        job_filter = JobFilter(llm_client)

        logger.info(
            "Refining filter for user %s: %d signals (%d decline, %d override), provider=%s",
            user.id,
            len(signals),
            len(decline_signals),
            len(override_signals),
            provider_override or "default",
        )

        result = await asyncio.to_thread(
            job_filter.generate_refinement,
            current_block,
            decline_signals,
            override_signals,
            user.id,
        )
    except Exception:
        logger.exception(
            "Refinement LLM call failed for user %s; leaving signals pending", user.id
        )
        return None

    proposal = RefinementProposal(
        proposed_learned_block=result["proposed_learned_block"],
        rationale=result["rationale"],
        signal_job_ids=[j.job_id for j in signals],
        decline_count=len(decline_signals),
        override_count=len(override_signals),
        created_at=datetime.now(tz=timezone.utc),
    )

    try:
        await ctx.user_repository.set_pending_proposal(user.id, proposal)
    except Exception:
        logger.exception(
            "Refinement: failed to store proposal for user %s; aborting cycle", user.id
        )
        return None

    # Mark these signals 'proposed' so they aren't re-fed next cycle.
    try:
        await ctx.repository.mark_refine_signals(proposal.signal_job_ids, "proposed")
    except Exception:
        logger.exception(
            "Refinement: failed to mark signals proposed for user %s", user.id
        )

    # Emit a persistent notification (best-effort — must not abort the cycle).
    if ctx.notification_repository is not None:
        try:
            await ctx.notification_repository.create(
                user.id,
                type=NOTIFICATION_TYPE,
                title="Filter improvement suggested",
                body=(
                    "Based on your recent declines and overrides, we drafted an "
                    "update to your job filter. Review it in Settings."
                ),
                action_url=FILTER_SETTINGS_URL,
            )
        except Exception:
            logger.exception(
                "Refinement: failed to create notification for user %s", user.id
            )

    logger.info("Refinement proposal created for user %s", user.id)
    return proposal


async def run_refinement_for_all(ctx: AppContext) -> int:
    """Run a refinement cycle for every opted-in user. Returns proposals created."""
    if ctx.user_repository is None:
        logger.warning("Refinement cycle skipped: no user repository")
        return 0

    try:
        users = await ctx.user_repository.get_all_with_auto_refine()
    except Exception:
        logger.exception("Refinement: failed to list opted-in users")
        return 0

    created = 0
    for user in users:
        proposal = await run_refinement_cycle(ctx, user)
        if proposal is not None:
            created += 1
    logger.info(
        "Refinement cycle complete: %d user(s) checked, %d proposal(s) created",
        len(users),
        created,
    )
    return created
