"""Tests for JobOrchestrator domain service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.context import AppContext
from src.models.state_machine import BusinessState
from src.models.unified import JobDescriptionInput, JobRecord, JobSubmitRequest
from src.services.jobs.job_orchestrator import JobOrchestrator

pytestmark = pytest.mark.asyncio

TEST_USER_ID = "user-abc-123"
TEST_MASTER_CV = {"contact": {"full_name": "Test User"}, "skills": ["Python"]}


def _make_ctx(**overrides) -> AppContext:
    """Create an AppContext with mock dependencies."""
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=None)
    repo.get_for_user = AsyncMock(return_value=None)
    prep_workflow = MagicMock()
    prep_workflow.ainvoke = AsyncMock(return_value={"current_step": "completed"})
    ctx = AppContext(
        repository=repo,
        settings=MagicMock(),
        prep_workflow=prep_workflow,
        retry_workflow=MagicMock(),
        job_queue=MagicMock(),
    )
    from src.agents.dispatcher import WorkflowDispatcher
    ctx.workflow_dispatcher = WorkflowDispatcher(ctx)
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


class TestSubmitJob:
    """Test JobOrchestrator.submit_job()."""

    async def test_submit_manual_job(self):
        ctx = _make_ctx()
        orchestrator = JobOrchestrator(ctx)

        request = JobSubmitRequest(
            source="manual",
            mode="mvp",
            job_description=JobDescriptionInput(
                title="Engineer",
                company="Acme",
                description="Build stuff",
                requirements="Python",
                template_name="compact",
            ),
        )

        response = await orchestrator.submit_job(request, TEST_USER_ID, TEST_MASTER_CV)

        assert response.status == "queued"
        assert response.job_id  # non-empty UUID
        assert "submitted successfully" in response.message

    async def test_submit_url_job(self):
        ctx = _make_ctx()
        orchestrator = JobOrchestrator(ctx)

        request = JobSubmitRequest(
            source="url",
            mode="full",
            url="https://example.com/job/123",
        )

        response = await orchestrator.submit_job(request, TEST_USER_ID, TEST_MASTER_CV)

        assert response.status == "queued"
        assert response.job_id

    async def test_submit_url_without_url_raises(self):
        ctx = _make_ctx()
        orchestrator = JobOrchestrator(ctx)

        request = JobSubmitRequest(source="url", mode="mvp")

        with pytest.raises(ValueError, match="URL is required"):
            await orchestrator.submit_job(request, TEST_USER_ID, TEST_MASTER_CV)

    async def test_submit_manual_without_description_raises(self):
        ctx = _make_ctx()
        orchestrator = JobOrchestrator(ctx)

        request = JobSubmitRequest(source="manual", mode="mvp")

        with pytest.raises(ValueError, match="job_description is required"):
            await orchestrator.submit_job(request, TEST_USER_ID, TEST_MASTER_CV)

    async def test_submit_registers_workflow(self):
        ctx = _make_ctx()
        orchestrator = JobOrchestrator(ctx)

        request = JobSubmitRequest(
            source="manual",
            mode="mvp",
            job_description=JobDescriptionInput(
                title="Engineer",
                company="Acme",
                description="Build stuff",
                requirements="Python",
            ),
        )

        response = await orchestrator.submit_job(request, TEST_USER_ID, TEST_MASTER_CV)

        # Verify workflow was registered
        thread_info = await ctx.get_workflow_thread(response.job_id)
        assert thread_info is not None
        assert thread_info["workflow_type"] == "preparation"


class TestProceedFilteredOut:
    """Test JobOrchestrator.proceed_filtered_out() ("Proceed Anyway")."""

    @staticmethod
    def _filtered_out_job() -> JobRecord:
        return JobRecord(
            job_id="job-fo-1",
            user_id=TEST_USER_ID,
            source="linkedin",
            mode="full",
            status="filtered_out",
            job_posting={"title": "Engineer", "company": "Acme", "description": "x" * 500},
            raw_input={"url": "https://linkedin.com/jobs/1"},
            filter_result={"score": 12, "disqualified": True, "reasoning": "nope"},
        )

    def _ctx_with_user(self, repo):
        ctx = _make_ctx(repository=repo)
        user = MagicMock()
        user.master_cv_json = TEST_MASTER_CV
        user.model_preferences = None
        user_repo = AsyncMock()
        user_repo.get_by_id = AsyncMock(return_value=user)
        ctx.user_repository = user_repo
        return ctx

    async def test_proceed_dispatches_and_sets_processing(self):
        job = self._filtered_out_job()
        repo = AsyncMock()
        repo.get_for_user = AsyncMock(return_value=job)
        repo.update = AsyncMock()
        ctx = self._ctx_with_user(repo)
        orchestrator = JobOrchestrator(ctx)

        response = await orchestrator.proceed_filtered_out("job-fo-1", TEST_USER_ID)

        assert response.status == "processing"
        assert response.job_id == "job-fo-1"
        # Status flipped to PROCESSING.
        repo.update.assert_awaited_once_with(
            "job-fo-1", {"status": BusinessState.PROCESSING}
        )
        # Workflow registered for the job.
        thread_info = await ctx.get_workflow_thread("job-fo-1")
        assert thread_info is not None
        assert thread_info["workflow_type"] == "preparation"

    async def test_proceed_preserves_filter_result_and_forces_full(self):
        job = self._filtered_out_job()
        repo = AsyncMock()
        repo.get_for_user = AsyncMock(return_value=job)
        repo.update = AsyncMock()
        ctx = self._ctx_with_user(repo)
        orchestrator = JobOrchestrator(ctx)

        captured = {}

        async def fake_ainvoke(initial_state, config):
            captured.update(initial_state)
            return {"current_step": "pending"}

        ctx.prep_workflow.ainvoke = AsyncMock(side_effect=fake_ainvoke)

        await orchestrator.proceed_filtered_out("job-fo-1", TEST_USER_ID)
        # Let the background dispatch task run.
        import asyncio as _asyncio
        await _asyncio.sleep(0.05)

        assert captured["skip_filter"] is True
        assert captured["mode"] == "full"
        assert captured["filter_result"] == job.filter_result
        assert captured["job_posting"] == job.job_posting

    async def test_proceed_non_filtered_raises(self):
        job = self._filtered_out_job()
        job.status = "pending"
        repo = AsyncMock()
        repo.get_for_user = AsyncMock(return_value=job)
        ctx = self._ctx_with_user(repo)
        orchestrator = JobOrchestrator(ctx)

        with pytest.raises(RuntimeError, match="not filtered out"):
            await orchestrator.proceed_filtered_out("job-fo-1", TEST_USER_ID)

    async def test_proceed_missing_job_raises(self):
        repo = AsyncMock()
        repo.get_for_user = AsyncMock(return_value=None)
        ctx = self._ctx_with_user(repo)
        orchestrator = JobOrchestrator(ctx)

        with pytest.raises(KeyError, match="not found"):
            await orchestrator.proceed_filtered_out("nope", TEST_USER_ID)


class TestGetStatus:
    """Test JobOrchestrator.get_status()."""

    async def test_status_from_repository(self):
        job = JobRecord(
            job_id="job-1",
            user_id=TEST_USER_ID,
            source="manual",
            mode="mvp",
            status="completed",
            current_cv_json={"name": "Test"},
            current_pdf_path="/tmp/test.pdf",
        )
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=job)
        repo.get_cv_attempts = AsyncMock(return_value=[])
        ctx = _make_ctx(repository=repo)
        orchestrator = JobOrchestrator(ctx)

        status = await orchestrator.get_status("job-1")

        assert status.job_id == "job-1"
        assert status.status == "completed"
        assert status.cv_json == {"name": "Test"}
        assert status.pdf_path == "/tmp/test.pdf"

    async def test_status_from_workflow_threads(self):
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=None)
        ctx = _make_ctx(repository=repo)
        orchestrator = JobOrchestrator(ctx)

        # Register a workflow thread
        await ctx.register_workflow("job-2", "thread-2", "preparation")

        # Mock get_state
        state_snapshot = MagicMock()
        state_snapshot.values = {
            "current_step": "composing_cv",
            "source": "manual",
            "mode": "mvp",
            "retry_count": 0,
        }
        ctx.prep_workflow.get_state.return_value = state_snapshot

        status = await orchestrator.get_status("job-2")

        assert status.job_id == "job-2"
        assert status.status == "composing_cv"
        assert status.source == "manual"

    async def test_status_not_found_raises(self):
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=None)
        ctx = _make_ctx(repository=repo)
        orchestrator = JobOrchestrator(ctx)

        with pytest.raises(KeyError, match="not found"):
            await orchestrator.get_status("nonexistent")

    async def test_status_retry_workflow_thread(self):
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=None)
        ctx = _make_ctx(repository=repo)
        orchestrator = JobOrchestrator(ctx)

        await ctx.register_workflow("job-3", "thread-3", "retry")

        state_snapshot = MagicMock()
        state_snapshot.values = {
            "current_step": "composing_cv",
            "source": "manual",
            "mode": "full",
            "retry_count": 1,
        }
        ctx.retry_workflow.get_state.return_value = state_snapshot

        status = await orchestrator.get_status("job-3")
        assert status.status == "composing_cv"
        assert status.attempt_count == 1
