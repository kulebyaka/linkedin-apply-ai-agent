"""Tests for HITLProcessor domain service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.context import AppContext
from src.models.unified import HITLDecision, JobRecord
from src.services.hitl_processor import HITLProcessor

pytestmark = pytest.mark.asyncio


def _make_ctx(**overrides) -> AppContext:
    """Create an AppContext with mock dependencies."""
    repo = AsyncMock()
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


def _make_pending_job(job_id: str = "job-1") -> JobRecord:
    return JobRecord(
        job_id=job_id,
        source="manual",
        mode="full",
        status="pending",
        job_posting={"title": "Engineer", "company": "Acme"},
        cv_json={"name": "Test CV"},
        pdf_path="/tmp/test.pdf",
    )


class TestProcessDecision:
    """Test HITLProcessor.process_decision()."""

    async def test_approve(self):
        job = _make_pending_job()
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=job)
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        result = await processor.process_decision(
            "job-1", HITLDecision(decision="approved")
        )

        assert result.status == "approved"
        assert result.job_id == "job-1"
        repo.update.assert_awaited_once_with("job-1", {"status": "approved"})

    async def test_decline(self):
        job = _make_pending_job()
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=job)
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        result = await processor.process_decision(
            "job-1", HITLDecision(decision="declined")
        )

        assert result.status == "declined"
        repo.update.assert_awaited_once_with("job-1", {"status": "declined"})

    @patch("src.agents.preparation_workflow.load_master_cv", return_value={"name": "Test"})
    async def test_retry_with_feedback(self, mock_cv):
        job = _make_pending_job()
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=job)
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        result = await processor.process_decision(
            "job-1", HITLDecision(decision="retry", feedback="Add more Python experience")
        )

        assert result.status == "retrying"
        repo.update.assert_awaited_once_with("job-1", {"status": "retrying"})

    async def test_retry_without_feedback_raises(self):
        job = _make_pending_job()
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=job)
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        with pytest.raises(ValueError, match="Feedback is required"):
            await processor.process_decision(
                "job-1", HITLDecision(decision="retry")
            )

    async def test_job_not_found_raises(self):
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=None)
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        with pytest.raises(KeyError, match="not found"):
            await processor.process_decision(
                "nonexistent", HITLDecision(decision="approved")
            )

    async def test_non_pending_job_raises(self):
        job = JobRecord(
            job_id="job-1",
            source="manual",
            mode="full",
            status="declined",
        )
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=job)
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        with pytest.raises(RuntimeError, match="not pending"):
            await processor.process_decision(
                "job-1", HITLDecision(decision="approved")
            )


class TestGetPending:
    """Test HITLProcessor.get_pending()."""

    async def test_returns_pending_jobs(self):
        jobs = [_make_pending_job("job-1"), _make_pending_job("job-2")]
        repo = AsyncMock()
        repo.get_pending = AsyncMock(return_value=jobs)
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        result = await processor.get_pending()

        assert len(result) == 2
        assert result[0].job_id == "job-1"
        assert result[1].job_id == "job-2"
        assert result[0].job_posting == {"title": "Engineer", "company": "Acme"}

    async def test_returns_empty_list(self):
        repo = AsyncMock()
        repo.get_pending = AsyncMock(return_value=[])
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        result = await processor.get_pending()
        assert result == []

    async def test_fallback_on_not_implemented(self):
        repo = AsyncMock()
        repo.get_pending = AsyncMock(side_effect=NotImplementedError)
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        # No workflow threads registered, so should return empty
        result = await processor.get_pending()
        assert result == []


class TestGetHistory:
    """Test HITLProcessor.get_history()."""

    async def test_returns_history(self):
        jobs = [
            JobRecord(
                job_id="job-1",
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

        result = await processor.get_history(limit=10)

        assert len(result) == 1
        assert result[0].job_id == "job-1"
        assert result[0].job_title == "Engineer"
        assert result[0].company == "Acme"

    async def test_with_status_filter(self):
        repo = AsyncMock()
        repo.get_history = AsyncMock(return_value=[])
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        await processor.get_history(limit=50, status="declined")

        repo.get_history.assert_awaited_once_with(limit=50, statuses=["declined"])

    async def test_fallback_on_not_implemented(self):
        repo = AsyncMock()
        repo.get_history = AsyncMock(side_effect=NotImplementedError)
        ctx = _make_ctx(repository=repo)
        processor = HITLProcessor(ctx)

        result = await processor.get_history()
        assert result == []
