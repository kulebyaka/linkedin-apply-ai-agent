"""WorkflowDispatcher — single entry point for invoking LangGraph workflows.

Centralizes the boilerplate that was previously duplicated across:
- ``JobOrchestrator._run_preparation_workflow`` (user-initiated submit)
- ``process_queue`` (queue consumer for LinkedIn-scraped jobs)
- ``_run_admin_retry`` (admin retry endpoint)
- ``HITLProcessor._run_retry_workflow`` (HITL retry decision)

Responsibilities:
- Build the ``config["configurable"]`` dict (thread_id + repositories).
- Register/unregister the workflow thread on ``AppContext``.
- On exception: persist a FAILED record, respecting ``ALLOWED_TRANSITIONS``
  so the workflow's own terminal writes (COMPLETED, PENDING, etc.)
  aren't clobbered.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.models.state_machine import ALLOWED_TRANSITIONS, BusinessState
from src.models.unified import JobRecord

if TYPE_CHECKING:
    from src.context import AppContext

logger = logging.getLogger(__name__)


class WorkflowDispatcher:
    """Invoke preparation/retry workflows with consistent tracking and recovery.

    Construct once per AppContext (typically attached to ``ctx.workflow_dispatcher``)
    and reuse across all dispatch sites.
    """

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx

    # ------------------------------------------------------------------
    # Preparation workflow
    # ------------------------------------------------------------------

    async def dispatch_preparation(
        self,
        *,
        job_id: str,
        thread_id: str,
        initial_state: dict[str, Any],
        user_id: str = "",
        track: bool = True,
        create_failure_record: bool = False,
    ) -> None:
        """Invoke the preparation workflow and persist FAILED on exception.

        Args:
            job_id: The job identifier (also the repository PK).
            thread_id: LangGraph thread_id; must be unique per invocation.
            initial_state: Workflow input state dict.
            user_id: User this dispatch belongs to (used for tracking + recovery).
            track: When True, register/unregister the workflow on AppContext
                so ``get_status`` can find in-progress jobs.
            create_failure_record: When True, if the workflow fails *before*
                ``save_to_db_node`` writes the record, synthesize a FAILED
                JobRecord so the failure is still visible.
        """
        config = {
            "configurable": {
                "thread_id": thread_id,
                "repository": self._ctx.repository,
                "user_repository": self._ctx.user_repository,
            }
        }

        if track:
            await self._ctx.register_workflow(
                job_id, thread_id, "preparation", user_id=user_id
            )

        try:
            result = await self._ctx.prep_workflow.ainvoke(initial_state, config)
            logger.info(
                "Preparation workflow for job %s completed: %s",
                job_id,
                result.get("current_step"),
            )
        except Exception as exc:
            await self._mark_preparation_failed(
                job_id=job_id,
                user_id=user_id,
                initial_state=initial_state,
                exc=exc,
                create_record_if_missing=create_failure_record,
            )
        finally:
            if track:
                await self._ctx.unregister_workflow(job_id)

    # ------------------------------------------------------------------
    # Retry workflow
    # ------------------------------------------------------------------

    async def dispatch_retry(
        self,
        *,
        job_id: str,
        thread_id: str,
        initial_state: dict[str, Any],
        user_id: str = "",
    ) -> None:
        """Invoke the retry workflow and persist FAILED on exception."""
        config = {
            "configurable": {
                "thread_id": thread_id,
                "repository": self._ctx.repository,
            }
        }

        await self._ctx.register_workflow(job_id, thread_id, "retry", user_id=user_id)

        try:
            result = await self._ctx.retry_workflow.ainvoke(initial_state, config)
            logger.info(
                "Retry workflow for job %s completed: %s",
                job_id,
                result.get("current_step"),
            )
        except Exception as exc:
            logger.exception("Retry workflow for job %s failed", job_id)
            try:
                await self._ctx.repository.update(
                    job_id,
                    {"status": BusinessState.FAILED, "error_message": str(exc)},
                )
            except Exception:
                logger.warning(
                    "Failed to mark job %s as FAILED in repository", job_id
                )
        finally:
            await self._ctx.unregister_workflow(job_id)

    # ------------------------------------------------------------------
    # Failure recovery
    # ------------------------------------------------------------------

    async def _mark_preparation_failed(
        self,
        *,
        job_id: str,
        user_id: str,
        initial_state: dict[str, Any],
        exc: BaseException,
        create_record_if_missing: bool,
    ) -> None:
        """Persist FAILED for a preparation workflow exception.

        Respects ALLOWED_TRANSITIONS: if the workflow already wrote a terminal
        status (e.g. COMPLETED), we don't clobber it. If the record never got
        created (workflow blew up before save_to_db_node) we optionally
        synthesize one so the failure isn't silently lost.
        """
        logger.error(
            "Preparation workflow for job %s failed: %s",
            job_id, exc, exc_info=True,
        )

        try:
            existing = await self._ctx.repository.get(job_id)
            if existing is not None:
                if BusinessState.FAILED in ALLOWED_TRANSITIONS.get(existing.status, set()):
                    await self._ctx.repository.update(
                        job_id,
                        {
                            "status": BusinessState.FAILED,
                            "error_message": str(exc),
                        },
                    )
                else:
                    logger.info(
                        "Skipping FAILED transition for job %s (current status %s is terminal)",
                        job_id, existing.status,
                    )
            elif create_record_if_missing:
                now = datetime.now(tz=timezone.utc)
                await self._ctx.repository.create(JobRecord(
                    job_id=job_id,
                    user_id=user_id or initial_state.get("user_id", ""),
                    source=initial_state.get("source", "url"),
                    mode=initial_state.get("mode", "mvp"),
                    status=BusinessState.FAILED,
                    raw_input=initial_state.get("raw_input") or {},
                    error_message=str(exc),
                    created_at=now,
                    updated_at=now,
                ))
        except Exception:
            logger.warning(
                "Failed to persist failure record for job %s", job_id, exc_info=True
            )
