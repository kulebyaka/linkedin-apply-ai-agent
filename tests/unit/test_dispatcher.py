"""Tests for WorkflowDispatcher — focused on the FAILED-recovery guard.

The dispatcher's contract on a workflow exception is:
- If the record exists and the current status *allows* a transition to FAILED
  (per ALLOWED_TRANSITIONS), write FAILED + error_message.
- If the record exists but the status is terminal or otherwise disallows
  FAILED, do NOT clobber the workflow's own terminal write.
- If the record does not exist and create_failure_record=True, synthesize
  a FAILED JobRecord so the failure isn't silently lost.
- If the record does not exist and create_failure_record=False, do nothing.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.dispatcher import WorkflowDispatcher
from src.context import AppContext
from src.models.state_machine import ALLOWED_TRANSITIONS, BusinessState
from src.models.unified import JobRecord

pytestmark = pytest.mark.asyncio

TEST_USER_ID = "user-abc-123"
TEST_JOB_ID = "job-1"
TEST_THREAD_ID = "thread-1"


def _make_ctx(*, prep_result=None, prep_exc=None, retry_result=None, retry_exc=None):
    """Build an AppContext with mock workflows + repository.

    Either ``prep_result`` (success) or ``prep_exc`` (failure) must be set
    for preparation tests; same for retry.
    """
    repo = AsyncMock()

    prep_workflow = MagicMock()
    if prep_exc is not None:
        prep_workflow.ainvoke = AsyncMock(side_effect=prep_exc)
    else:
        prep_workflow.ainvoke = AsyncMock(
            return_value=prep_result or {"current_step": "completed"}
        )

    retry_workflow = MagicMock()
    if retry_exc is not None:
        retry_workflow.ainvoke = AsyncMock(side_effect=retry_exc)
    else:
        retry_workflow.ainvoke = AsyncMock(
            return_value=retry_result or {"current_step": "pending"}
        )

    ctx = AppContext(
        repository=repo,
        settings=MagicMock(),
        prep_workflow=prep_workflow,
        retry_workflow=retry_workflow,
    )
    ctx.workflow_dispatcher = WorkflowDispatcher(ctx)
    return ctx


def _make_record(status: BusinessState, job_id: str = TEST_JOB_ID) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        user_id=TEST_USER_ID,
        source="manual",
        mode="full",
        status=status,
    )


def _initial_state(**overrides) -> dict:
    base = {
        "job_id": TEST_JOB_ID,
        "user_id": TEST_USER_ID,
        "source": "manual",
        "mode": "full",
        "raw_input": {"description": "some job"},
    }
    base.update(overrides)
    return base


# ============================================================================
# Happy path: workflow succeeds, no FAILED write
# ============================================================================


class TestPreparationSuccess:
    async def test_no_failed_write_when_workflow_completes(self):
        ctx = _make_ctx(prep_result={"current_step": "completed"})
        dispatcher = ctx.workflow_dispatcher

        await dispatcher.dispatch_preparation(
            job_id=TEST_JOB_ID,
            thread_id=TEST_THREAD_ID,
            initial_state=_initial_state(),
            user_id=TEST_USER_ID,
        )

        # Workflow invoked, no failure recovery path taken
        ctx.prep_workflow.ainvoke.assert_awaited_once()
        ctx.repository.get.assert_not_called()
        ctx.repository.update.assert_not_called()
        ctx.repository.create.assert_not_called()

    async def test_workflow_thread_registered_and_unregistered(self):
        ctx = _make_ctx(prep_result={"current_step": "completed"})
        dispatcher = ctx.workflow_dispatcher

        await dispatcher.dispatch_preparation(
            job_id=TEST_JOB_ID,
            thread_id=TEST_THREAD_ID,
            initial_state=_initial_state(),
            user_id=TEST_USER_ID,
        )

        # After successful completion, no in-progress workflow remains tracked
        assert await ctx.get_workflow_thread(TEST_JOB_ID) is None

    async def test_track_false_skips_registration(self):
        ctx = _make_ctx(prep_result={"current_step": "completed"})
        dispatcher = ctx.workflow_dispatcher

        await dispatcher.dispatch_preparation(
            job_id=TEST_JOB_ID,
            thread_id=TEST_THREAD_ID,
            initial_state=_initial_state(),
            user_id=TEST_USER_ID,
            track=False,
        )

        # Tracking dict is never written when track=False
        assert await ctx.get_all_workflow_threads() == {}


# ============================================================================
# Guard: workflow fails, existing record allows FAILED transition
# ============================================================================


class TestFailedTransitionAllowed:
    @pytest.mark.parametrize(
        "current_status",
        [
            BusinessState.QUEUED,
            BusinessState.PROCESSING,
            BusinessState.APPROVED,
            BusinessState.APPLYING,
            BusinessState.RETRYING,
            BusinessState.SCRAPE_FAILED,
        ],
    )
    async def test_failed_write_when_transition_allowed(self, current_status):
        """When ALLOWED_TRANSITIONS permits the move to FAILED, update happens."""
        # Sanity-check the parametrization matches the source of truth
        assert BusinessState.FAILED in ALLOWED_TRANSITIONS[current_status]

        ctx = _make_ctx(prep_exc=RuntimeError("LLM blew up"))
        ctx.repository.get = AsyncMock(return_value=_make_record(current_status))
        dispatcher = ctx.workflow_dispatcher

        await dispatcher.dispatch_preparation(
            job_id=TEST_JOB_ID,
            thread_id=TEST_THREAD_ID,
            initial_state=_initial_state(),
            user_id=TEST_USER_ID,
        )

        ctx.repository.get.assert_awaited_once_with(TEST_JOB_ID)
        ctx.repository.update.assert_awaited_once_with(
            TEST_JOB_ID,
            {
                "status": BusinessState.FAILED,
                "error_message": "LLM blew up",
            },
        )
        # No synthetic record needed — the existing row was updated
        ctx.repository.create.assert_not_called()


# ============================================================================
# Guard: workflow fails, existing record is terminal — DO NOT clobber
# ============================================================================


class TestFailedTransitionBlocked:
    @pytest.mark.parametrize(
        "terminal_status",
        [
            BusinessState.COMPLETED,
            BusinessState.DECLINED,
            BusinessState.APPLIED,
            BusinessState.FILTERED_OUT,
        ],
    )
    async def test_terminal_status_not_clobbered(self, terminal_status):
        """If the workflow already wrote a terminal status, don't overwrite it.

        This is the core regression guard the dispatcher exists to enforce:
        a late-firing exception must not corrupt a successful terminal write.
        """
        # Sanity-check the parametrization
        assert not ALLOWED_TRANSITIONS[terminal_status]

        ctx = _make_ctx(prep_exc=RuntimeError("post-success exception"))
        ctx.repository.get = AsyncMock(return_value=_make_record(terminal_status))
        dispatcher = ctx.workflow_dispatcher

        await dispatcher.dispatch_preparation(
            job_id=TEST_JOB_ID,
            thread_id=TEST_THREAD_ID,
            initial_state=_initial_state(),
            user_id=TEST_USER_ID,
        )

        ctx.repository.get.assert_awaited_once_with(TEST_JOB_ID)
        ctx.repository.update.assert_not_called()
        ctx.repository.create.assert_not_called()

    async def test_pending_status_not_clobbered(self):
        """PENDING is non-terminal but disallows direct FAILED transition.

        PENDING → FAILED is not in ALLOWED_TRANSITIONS (only APPROVED, DECLINED,
        RETRYING). The dispatcher must respect this even though PENDING isn't
        a terminal state.
        """
        assert BusinessState.FAILED not in ALLOWED_TRANSITIONS[BusinessState.PENDING]

        ctx = _make_ctx(prep_exc=RuntimeError("oops"))
        ctx.repository.get = AsyncMock(return_value=_make_record(BusinessState.PENDING))
        dispatcher = ctx.workflow_dispatcher

        await dispatcher.dispatch_preparation(
            job_id=TEST_JOB_ID,
            thread_id=TEST_THREAD_ID,
            initial_state=_initial_state(),
            user_id=TEST_USER_ID,
        )

        ctx.repository.update.assert_not_called()


# ============================================================================
# Synthetic record path: workflow fails before save_to_db_node ran
# ============================================================================


class TestSyntheticFailureRecord:
    async def test_create_record_if_missing_true(self):
        """When the record was never created, synthesize one as FAILED."""
        ctx = _make_ctx(prep_exc=RuntimeError("extract failed"))
        ctx.repository.get = AsyncMock(return_value=None)
        dispatcher = ctx.workflow_dispatcher

        await dispatcher.dispatch_preparation(
            job_id=TEST_JOB_ID,
            thread_id=TEST_THREAD_ID,
            initial_state=_initial_state(source="url", mode="mvp"),
            user_id=TEST_USER_ID,
            create_failure_record=True,
        )

        ctx.repository.get.assert_awaited_once_with(TEST_JOB_ID)
        ctx.repository.update.assert_not_called()
        ctx.repository.create.assert_awaited_once()

        created_record = ctx.repository.create.await_args.args[0]
        assert isinstance(created_record, JobRecord)
        assert created_record.job_id == TEST_JOB_ID
        assert created_record.user_id == TEST_USER_ID
        assert created_record.status == BusinessState.FAILED
        assert created_record.error_message == "extract failed"
        assert created_record.source == "url"
        assert created_record.mode == "mvp"

    async def test_create_record_if_missing_false(self):
        """Without the flag, missing records stay missing — failure is logged only."""
        ctx = _make_ctx(prep_exc=RuntimeError("extract failed"))
        ctx.repository.get = AsyncMock(return_value=None)
        dispatcher = ctx.workflow_dispatcher

        await dispatcher.dispatch_preparation(
            job_id=TEST_JOB_ID,
            thread_id=TEST_THREAD_ID,
            initial_state=_initial_state(),
            user_id=TEST_USER_ID,
            create_failure_record=False,
        )

        ctx.repository.get.assert_awaited_once_with(TEST_JOB_ID)
        ctx.repository.update.assert_not_called()
        ctx.repository.create.assert_not_called()

    async def test_synthetic_record_falls_back_to_initial_state_user_id(self):
        """If user_id arg is empty, fall back to initial_state['user_id']."""
        ctx = _make_ctx(prep_exc=RuntimeError("boom"))
        ctx.repository.get = AsyncMock(return_value=None)
        dispatcher = ctx.workflow_dispatcher

        await dispatcher.dispatch_preparation(
            job_id=TEST_JOB_ID,
            thread_id=TEST_THREAD_ID,
            initial_state=_initial_state(user_id="state-user"),
            user_id="",
            create_failure_record=True,
        )

        created_record = ctx.repository.create.await_args.args[0]
        assert created_record.user_id == "state-user"


# ============================================================================
# Resilience: recovery itself must not raise
# ============================================================================


class TestRecoveryResilience:
    async def test_repository_get_failure_is_swallowed(self):
        """If get() itself raises, the dispatcher must not propagate it.

        The outer exception (the workflow failure) has already been logged;
        a secondary failure in the recovery path should be logged and
        swallowed so the caller doesn't see a misleading error.
        """
        ctx = _make_ctx(prep_exc=RuntimeError("workflow boom"))
        ctx.repository.get = AsyncMock(side_effect=RuntimeError("db connection lost"))
        dispatcher = ctx.workflow_dispatcher

        # Should not raise
        await dispatcher.dispatch_preparation(
            job_id=TEST_JOB_ID,
            thread_id=TEST_THREAD_ID,
            initial_state=_initial_state(),
            user_id=TEST_USER_ID,
        )

        # Tracking is still cleaned up despite recovery failure
        assert await ctx.get_workflow_thread(TEST_JOB_ID) is None

    async def test_tracking_cleaned_up_on_workflow_failure(self):
        """The `finally` block must unregister even when recovery succeeds."""
        ctx = _make_ctx(prep_exc=RuntimeError("workflow boom"))
        ctx.repository.get = AsyncMock(return_value=_make_record(BusinessState.QUEUED))
        dispatcher = ctx.workflow_dispatcher

        await dispatcher.dispatch_preparation(
            job_id=TEST_JOB_ID,
            thread_id=TEST_THREAD_ID,
            initial_state=_initial_state(),
            user_id=TEST_USER_ID,
        )

        assert await ctx.get_workflow_thread(TEST_JOB_ID) is None


# ============================================================================
# Retry workflow: simpler path, no transition guard
# ============================================================================


class TestRetryDispatch:
    async def test_retry_success_no_failed_write(self):
        ctx = _make_ctx(retry_result={"current_step": "pending"})
        dispatcher = ctx.workflow_dispatcher

        await dispatcher.dispatch_retry(
            job_id=TEST_JOB_ID,
            thread_id=TEST_THREAD_ID,
            initial_state=_initial_state(),
            user_id=TEST_USER_ID,
        )

        ctx.retry_workflow.ainvoke.assert_awaited_once()
        ctx.repository.update.assert_not_called()

    async def test_retry_failure_writes_failed(self):
        ctx = _make_ctx(retry_exc=RuntimeError("retry blew up"))
        dispatcher = ctx.workflow_dispatcher

        await dispatcher.dispatch_retry(
            job_id=TEST_JOB_ID,
            thread_id=TEST_THREAD_ID,
            initial_state=_initial_state(),
            user_id=TEST_USER_ID,
        )

        ctx.repository.update.assert_awaited_once_with(
            TEST_JOB_ID,
            {"status": BusinessState.FAILED, "error_message": "retry blew up"},
        )

    async def test_retry_failure_swallows_secondary_update_error(self):
        """If the FAILED update itself fails, the dispatcher must not propagate."""
        ctx = _make_ctx(retry_exc=RuntimeError("retry blew up"))
        ctx.repository.update = AsyncMock(side_effect=RuntimeError("db down"))
        dispatcher = ctx.workflow_dispatcher

        # Should not raise
        await dispatcher.dispatch_retry(
            job_id=TEST_JOB_ID,
            thread_id=TEST_THREAD_ID,
            initial_state=_initial_state(),
            user_id=TEST_USER_ID,
        )

        assert await ctx.get_workflow_thread(TEST_JOB_ID) is None
