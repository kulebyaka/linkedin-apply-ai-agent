"""Tests for JobOrchestrator domain service."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.context import AppContext
from src.models.unified import JobDescriptionInput
from src.models.unified import JobRecord, JobSubmitRequest
from src.services.job_orchestrator import JobOrchestrator

pytestmark = pytest.mark.asyncio


def _make_ctx(**overrides) -> AppContext:
    """Create an AppContext with mock dependencies."""
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=None)
    ctx = AppContext(
        repository=repo,
        settings=MagicMock(),
        prep_workflow=MagicMock(),
        retry_workflow=MagicMock(),
        job_queue=MagicMock(),
    )
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


class TestSubmitJob:
    """Test JobOrchestrator.submit_job()."""

    @patch("src.agents.preparation_workflow.load_master_cv", return_value={"name": "Test"})
    async def test_submit_manual_job(self, mock_cv):
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

        response = await orchestrator.submit_job(request)

        assert response.status == "queued"
        assert response.job_id  # non-empty UUID
        assert "submitted successfully" in response.message

    @patch("src.agents.preparation_workflow.load_master_cv", return_value={"name": "Test"})
    async def test_submit_url_job(self, mock_cv):
        ctx = _make_ctx()
        orchestrator = JobOrchestrator(ctx)

        request = JobSubmitRequest(
            source="url",
            mode="full",
            url="https://example.com/job/123",
        )

        response = await orchestrator.submit_job(request)

        assert response.status == "queued"
        assert response.job_id

    async def test_submit_url_without_url_raises(self):
        ctx = _make_ctx()
        orchestrator = JobOrchestrator(ctx)

        request = JobSubmitRequest(source="url", mode="mvp")

        with pytest.raises(ValueError, match="URL is required"):
            await orchestrator.submit_job(request)

    async def test_submit_manual_without_description_raises(self):
        ctx = _make_ctx()
        orchestrator = JobOrchestrator(ctx)

        request = JobSubmitRequest(source="manual", mode="mvp")

        with pytest.raises(ValueError, match="job_description is required"):
            await orchestrator.submit_job(request)

    @patch("src.agents.preparation_workflow.load_master_cv", return_value={"name": "Test"})
    async def test_submit_registers_workflow(self, mock_cv):
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

        response = await orchestrator.submit_job(request)

        # Verify workflow was registered
        thread_info = await ctx.get_workflow_thread(response.job_id)
        assert thread_info is not None
        assert thread_info["workflow_type"] == "preparation"


class TestGetStatus:
    """Test JobOrchestrator.get_status()."""

    async def test_status_from_repository(self):
        job = JobRecord(
            job_id="job-1",
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
