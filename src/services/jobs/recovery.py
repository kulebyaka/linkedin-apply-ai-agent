"""Startup recovery for jobs left in non-terminal states.

When the process restarts, any `JobRecord` with status QUEUED / PROCESSING /
RETRYING is mid-flight from the previous run. The in-memory `JobQueue` lost
those entries, so without intervention they would sit forever in the DB and
the user would never see a terminal outcome.

`recover_in_flight_jobs` is invoked once during FastAPI lifespan startup
after the AppContext is built and the consumer/scheduler are wired. For each
in-flight row it either re-enqueues (LinkedIn source → JobQueue) or
re-dispatches the preparation workflow directly (url / manual sources).
A bounded `recovery_attempts` counter prevents poison rows from looping.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.models.job import ScrapedJob
from src.models.state_machine import BusinessState

if TYPE_CHECKING:
    from src.context import AppContext

logger = logging.getLogger(__name__)


MAX_RECOVERY_ATTEMPTS = 3


@dataclass
class RecoveryReport:
    """Summary of a recovery pass; returned for telemetry/tests."""

    recovered: int = 0
    exhausted: int = 0
    skipped: int = 0


async def recover_in_flight_jobs(ctx: AppContext) -> RecoveryReport:
    """Re-enqueue or re-dispatch every non-terminal job in the repository.

    Idempotent across restarts thanks to the `recovery_attempts` counter on
    each row. Never raises — failures here are logged but must not block app
    startup (the system can still process new scrapes/submissions).
    """
    report = RecoveryReport()

    try:
        in_flight_states = [
            BusinessState.QUEUED,
            BusinessState.PROCESSING,
            BusinessState.RETRYING,
        ]
        rows = await ctx.repository.list_by_states(in_flight_states)
    except Exception:
        logger.exception("recover_in_flight_jobs: list_by_states failed; skipping")
        return report

    if not rows:
        logger.info("Startup recovery: no in-flight jobs to recover")
        return report

    # Anything currently tracked in-memory is "live" in this process; nothing
    # to do. (Always empty on a fresh boot; this guards a re-invocation case.)
    try:
        active = set((await ctx.get_all_workflow_threads()).keys())
    except Exception:
        active = set()

    now = datetime.now(tz=timezone.utc)

    for row in rows:
        if row.job_id in active:
            report.skipped += 1
            continue

        next_attempts = (row.recovery_attempts or 0) + 1
        if next_attempts > MAX_RECOVERY_ATTEMPTS:
            logger.warning(
                "Recovery cap exhausted for job %s (status=%s, attempts=%d); marking FAILED",
                row.job_id, row.status, row.recovery_attempts,
            )
            try:
                await ctx.repository.update(
                    row.job_id,
                    {
                        "status": BusinessState.FAILED,
                        "error_message": "restart loop guard: recovery_attempts exhausted",
                        "last_recovery_attempt_at": now,
                    },
                )
            except Exception:
                logger.warning(
                    "Failed to mark recovery-exhausted job %s as FAILED",
                    row.job_id, exc_info=True,
                )
            report.exhausted += 1
            continue

        try:
            await ctx.repository.update(
                row.job_id,
                {
                    "recovery_attempts": next_attempts,
                    "last_recovery_attempt_at": now,
                },
            )
        except Exception:
            logger.warning(
                "Failed to stamp recovery_attempts on %s; continuing",
                row.job_id, exc_info=True,
            )

        dispatched = await _redispatch(ctx, row)
        if dispatched:
            report.recovered += 1
        else:
            report.skipped += 1

    logger.info(
        "Startup recovery complete: recovered=%d exhausted=%d skipped=%d",
        report.recovered, report.exhausted, report.skipped,
    )
    return report


async def _redispatch(ctx: AppContext, row) -> bool:
    """Route a single recovered row back into its source's pipeline.

    Returns True if work was scheduled, False otherwise.
    """
    raw_input = row.raw_input or {}

    if row.source == "linkedin":
        if ctx.job_queue is None:
            logger.warning(
                "Cannot recover LinkedIn job %s — job_queue not initialized",
                row.job_id,
            )
            return False
        try:
            scraped = ScrapedJob.model_validate(raw_input)
        except Exception:
            logger.warning(
                "Cannot recover LinkedIn job %s — raw_input not a ScrapedJob",
                row.job_id, exc_info=True,
            )
            return False
        try:
            await ctx.job_queue.put(scraped, user_id=row.user_id or None)
            logger.info(
                "Recovered LinkedIn job %s by re-enqueueing (status=%s)",
                row.job_id, row.status,
            )
            return True
        except Exception:
            logger.warning(
                "Failed to re-enqueue LinkedIn job %s", row.job_id, exc_info=True
            )
            return False

    # url / manual sources go directly through the dispatcher
    dispatcher = ctx.workflow_dispatcher
    if dispatcher is None:
        logger.warning(
            "Cannot recover job %s — workflow_dispatcher not initialized",
            row.job_id,
        )
        return False

    initial_state = {
        "job_id": row.job_id,
        "user_id": row.user_id or "",
        "source": row.source,
        "mode": row.mode,
        "raw_input": raw_input,
        "job_posting": row.job_posting or {},
        "master_cv": {},
        "tailored_cv_json": {},
        "tailored_cv_pdf_path": "",
        "user_feedback": None,
        "retry_count": 0,
        "filter_result": row.filter_result,
        "current_step": BusinessState.QUEUED,
        "error_message": None,
    }

    # Try to pre-load the user's master CV so the recovered workflow can
    # actually compose. Falling back to filesystem is OK for legacy users.
    try:
        if row.user_id and ctx.user_repository is not None:
            user = await ctx.user_repository.get_by_id(row.user_id)
            if user and user.master_cv_json:
                initial_state["master_cv"] = user.master_cv_json
        if not initial_state["master_cv"]:
            from src.agents._shared import load_master_cv
            initial_state["master_cv"] = load_master_cv()
    except Exception:
        logger.warning(
            "Failed to load master CV for recovered job %s; continuing with empty",
            row.job_id, exc_info=True,
        )

    thread_id = f"recover-{row.job_id}-{uuid.uuid4().hex[:8]}"
    ctx.create_background_task(
        dispatcher.dispatch_preparation(
            job_id=row.job_id,
            thread_id=thread_id,
            initial_state=initial_state,
            user_id=row.user_id or "",
            create_failure_record=False,
        )
    )
    logger.info(
        "Recovered job %s by dispatching preparation workflow (source=%s, status=%s)",
        row.job_id, row.source, row.status,
    )
    return True
