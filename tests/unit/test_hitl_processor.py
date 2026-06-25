"""Tests for HITLProcessor domain service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.context import AppContext
from src.models.unified import HITLDecision, JobRecord
from src.services.jobs.hitl_processor import HITLProcessor

pytestmark = pytest.mark.asyncio

TEST_USER_ID = "user-abc-123"


def _make_ctx(**overrides) -> AppContext:
    """Create an AppContext with mock dependencies."""
    repo = AsyncMock()
    prep_workflow = MagicMock()
    prep_workflow.ainvoke = AsyncMock(return_value={"current_step": "completed"})
    retry_workflow = MagicMock()
    retry_workflow.ainvoke = AsyncMock(return_value={"current_step": "pending"})
    ctx = AppContext(
        repository=repo,
        settings=MagicMock(),
        prep_workflow=prep_workflow,
        retry_workflow=retry_workflow,
        job_queue=MagicMock(),
    )
    from src.agents.dispatcher import WorkflowDispatcher

    ctx.workflow_dispatcher = WorkflowDispatcher(ctx)
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


def _make_pending_job(job_id: str = "job-1") -> JobRecord:
    # LinkedIn Easy Apply job (source + URL) so approve dispatches the apply.
    # Non-LinkedIn jobs are diverted to manual_required by trigger_apply.
    return JobRecord(
        job_id=job_id,
        user_id=TEST_USER_ID,
        source="linkedin",
        mode="full",
        status="pending",
        job_posting={"title": "Engineer", "company": "Acme"},
        application_url="https://www.linkedin.com/jobs/view/1",
        current_cv_json={"name": "Test CV"},
        current_pdf_path="/tmp/test.pdf",
    )


class TestProcessDecision:
    """Test HITLProcessor.process_decision()."""

    async def test_approve_no_extension_parks_needs_extension(self):
        """Approve with no connected extension session → needs_extension (fail-fast)."""
        job = _make_pending_job()
        repo = AsyncMock()
        repo.get_for_user = AsyncMock(return_value=job)
        session_store = AsyncMock()
        session_store.is_connected = AsyncMock(return_value=False)
        ctx = _make_ctx(repository=repo, session_store=session_store)
        processor = HITLProcessor(ctx)

        result = await processor.process_decision(
            "job-1", HITLDecision(decision="approved"), TEST_USER_ID
        )

        assert result.status == "needs_extension"
        assert result.job_id == "job-1"
        # First sets APPROVED, then trigger_apply parks it in NEEDS_EXTENSION.
        statuses = [c.args[1]["status"] for c in repo.update.await_args_list]
        assert statuses == ["approved", "needs_extension"]

    async def test_approve_connected_dispatches_apply(self):
        """Approve with a connected session → APPLYING + apply workflow dispatched."""
        job = _make_pending_job()
        repo = AsyncMock()
        repo.get_for_user = AsyncMock(return_value=job)
        session_store = AsyncMock()
        session_store.is_connected = AsyncMock(return_value=True)
        user_repo = AsyncMock()
        user_repo.get_by_id = AsyncMock(
            return_value=MagicMock(
                apply_profile=None, master_cv_json={"contact": {"full_name": "T"}}
            )
        )
        ctx = _make_ctx(repository=repo, session_store=session_store, user_repository=user_repo)
        ctx.workflow_dispatcher = MagicMock()
        ctx.workflow_dispatcher.dispatch_application = AsyncMock()
        processor = HITLProcessor(ctx)

        result = await processor.process_decision(
            "job-1", HITLDecision(decision="approved"), TEST_USER_ID
        )

        assert result.status == "applying"
        # HITLProcessor sets APPROVED via update(); trigger_apply then claims the
        # APPLYING transition atomically through try_claim_for_apply (not update).
        statuses = [c.args[1]["status"] for c in repo.update.await_args_list]
        assert statuses == ["approved"]
        repo.try_claim_for_apply.assert_awaited_once_with("job-1")
        ctx.workflow_dispatcher.dispatch_application.assert_called_once()

    async def test_decline(self):
        job = _make_pending_job()
        repo = AsyncMock()
        repo.get_for_user = AsyncMock(return_value=job)
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        result = await processor.process_decision(
            "job-1", HITLDecision(decision="declined"), TEST_USER_ID
        )

        assert result.status == "declined"
        repo.update.assert_awaited_once_with("job-1", {"status": "declined"})

    async def test_retry_with_feedback(self):
        job = _make_pending_job()
        repo = AsyncMock()
        repo.get_for_user = AsyncMock(return_value=job)
        repo.get_cv_attempts = AsyncMock(return_value=[])
        user_repo = AsyncMock()
        user_repo.get_by_id = AsyncMock(
            return_value=MagicMock(master_cv_json={"contact": {"full_name": "Test"}})
        )
        ctx = _make_ctx(repository=repo, user_repository=user_repo)
        processor = HITLProcessor(ctx)

        result = await processor.process_decision(
            "job-1",
            HITLDecision(decision="retry", feedback="Add more Python experience"),
            TEST_USER_ID,
        )

        assert result.status == "retrying"
        repo.update.assert_awaited_once_with("job-1", {"status": "retrying"})

    async def test_retry_without_feedback_raises(self):
        job = _make_pending_job()
        repo = AsyncMock()
        repo.get_for_user = AsyncMock(return_value=job)
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        with pytest.raises(ValueError, match="Feedback is required"):
            await processor.process_decision("job-1", HITLDecision(decision="retry"), TEST_USER_ID)

    async def test_job_not_found_raises(self):
        repo = AsyncMock()
        repo.get_for_user = AsyncMock(return_value=None)
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        with pytest.raises(KeyError, match="not found"):
            await processor.process_decision(
                "nonexistent", HITLDecision(decision="approved"), TEST_USER_ID
            )

    async def test_non_pending_job_raises(self):
        job = JobRecord(
            job_id="job-1",
            user_id=TEST_USER_ID,
            source="manual",
            mode="full",
            status="declined",
        )
        repo = AsyncMock()
        repo.get_for_user = AsyncMock(return_value=job)
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        with pytest.raises(RuntimeError, match="not pending"):
            await processor.process_decision(
                "job-1", HITLDecision(decision="approved"), TEST_USER_ID
            )


class TestGetPending:
    """Test HITLProcessor.get_pending()."""

    async def test_returns_pending_jobs(self):
        jobs = [_make_pending_job("job-1"), _make_pending_job("job-2")]
        repo = AsyncMock()
        repo.get_pending = AsyncMock(return_value=jobs)
        repo.get_cv_attempts = AsyncMock(return_value=[])
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        result = await processor.get_pending(TEST_USER_ID)

        assert len(result) == 2
        assert result[0].job_id == "job-1"
        assert result[1].job_id == "job-2"
        assert result[0].job_posting == {"title": "Engineer", "company": "Acme"}
        assert result[0].attempt_count == 0
        repo.get_pending.assert_awaited_once_with(TEST_USER_ID)

    async def test_returns_empty_list(self):
        repo = AsyncMock()
        repo.get_pending = AsyncMock(return_value=[])
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        result = await processor.get_pending(TEST_USER_ID)
        assert result == []

    async def test_propagates_repository_error(self):
        repo = AsyncMock()
        repo.get_pending = AsyncMock(side_effect=NotImplementedError)
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        with pytest.raises(NotImplementedError):
            await processor.get_pending(TEST_USER_ID)


class TestGetHistory:
    """Test HITLProcessor.get_history()."""

    async def test_returns_history(self):
        jobs = [
            JobRecord(
                job_id="job-1",
                user_id=TEST_USER_ID,
                source="manual",
                mode="full",
                status="approved",
                job_posting={"title": "Engineer", "company": "Acme"},
            ),
        ]
        repo = AsyncMock()
        repo.get_history = AsyncMock(return_value=jobs)
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        result = await processor.get_history(TEST_USER_ID, limit=10)

        assert len(result) == 1
        assert result[0].job_id == "job-1"
        assert result[0].job_title == "Engineer"
        assert result[0].company == "Acme"

    async def test_with_status_filter(self):
        repo = AsyncMock()
        repo.get_history = AsyncMock(return_value=[])
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        await processor.get_history(TEST_USER_ID, limit=50, status="declined")

        repo.get_history.assert_awaited_once_with(
            user_id=TEST_USER_ID, limit=50, statuses=["declined"]
        )

    async def test_propagates_repository_error(self):
        repo = AsyncMock()
        repo.get_history = AsyncMock(side_effect=NotImplementedError)
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        with pytest.raises(NotImplementedError):
            await processor.get_history(TEST_USER_ID)
