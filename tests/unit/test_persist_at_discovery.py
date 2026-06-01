"""Tests for the 'persist jobs at discovery' feature.

Covers:
- ``JobRepository.list_by_states`` (in-memory).
- ``JobOrchestrator.submit_job`` writing a QUEUED row before dispatch.
- ``recover_in_flight_jobs`` happy path + recovery_attempts cap.
- ``HITLProcessor.get_pending`` honoring the ``states`` filter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.context import AppContext
from src.models.job import ScrapedJob
from src.models.state_machine import BusinessState
from src.models.unified import JobDescriptionInput, JobRecord, JobSubmitRequest
from src.services.db.in_memory_repository import InMemoryJobRepository
from src.services.jobs.hitl_processor import HITLProcessor
from src.services.jobs.job_orchestrator import JobOrchestrator
from src.services.jobs.recovery import MAX_RECOVERY_ATTEMPTS, recover_in_flight_jobs

pytestmark = pytest.mark.asyncio


def _record(
    job_id: str,
    *,
    status: BusinessState = BusinessState.QUEUED,
    user_id: str = "u1",
    source: str = "linkedin",
    recovery_attempts: int = 0,
    raw_input: dict | None = None,
) -> JobRecord:
    now = datetime.now(tz=timezone.utc)
    return JobRecord(
        job_id=job_id,
        user_id=user_id,
        source=source,  # type: ignore[arg-type]
        mode="full",
        status=status,
        job_posting={"title": "T", "company": "C"},
        raw_input=raw_input
        or {
            "job_id": job_id,
            "title": "T",
            "company": "C",
            "location": "L",
            "url": "https://example.com/" + job_id,
        },
        recovery_attempts=recovery_attempts,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# list_by_states
# ---------------------------------------------------------------------------


class TestListByStates:
    async def test_filters_by_states_and_user(self):
        repo = InMemoryJobRepository()
        await repo.initialize()
        await repo.create(_record("a", status=BusinessState.QUEUED, user_id="u1"))
        await repo.create(_record("b", status=BusinessState.PROCESSING, user_id="u1"))
        await repo.create(_record("c", status=BusinessState.PENDING, user_id="u1"))
        await repo.create(_record("d", status=BusinessState.QUEUED, user_id="u2"))

        u1_in_flight = await repo.list_by_states(
            ["queued", "processing"], user_id="u1"
        )
        ids = sorted(j.job_id for j in u1_in_flight)
        assert ids == ["a", "b"]

        global_queued = await repo.list_by_states(["queued"])
        ids = sorted(j.job_id for j in global_queued)
        assert ids == ["a", "d"]


# ---------------------------------------------------------------------------
# JobOrchestrator.submit_job
# ---------------------------------------------------------------------------


def _ctx_with_inmemory_repo(repo: InMemoryJobRepository) -> AppContext:
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
    return ctx


class TestSubmitJobPersistsQueued:
    async def test_manual_submission_creates_queued_row_before_dispatch(self):
        repo = InMemoryJobRepository()
        await repo.initialize()
        ctx = _ctx_with_inmemory_repo(repo)
        orchestrator = JobOrchestrator(ctx)

        request = JobSubmitRequest(
            source="manual",
            mode="full",
            job_description=JobDescriptionInput(
                title="Engineer",
                company="Acme",
                description="Build stuff",
                requirements="Python",
            ),
        )

        response = await orchestrator.submit_job(request, "u1", {"x": 1})

        row = await repo.get(response.job_id)
        assert row is not None
        assert row.status == BusinessState.QUEUED
        assert row.user_id == "u1"
        assert row.job_posting and row.job_posting["title"] == "Engineer"

    async def test_url_submission_creates_queued_row(self):
        repo = InMemoryJobRepository()
        await repo.initialize()
        ctx = _ctx_with_inmemory_repo(repo)
        orchestrator = JobOrchestrator(ctx)

        request = JobSubmitRequest(
            source="url",
            mode="mvp",
            url="https://example.com/job/123",
        )

        response = await orchestrator.submit_job(request, "u1", {"x": 1})

        row = await repo.get(response.job_id)
        assert row is not None
        assert row.status == BusinessState.QUEUED
        assert row.source == "url"


# ---------------------------------------------------------------------------
# recover_in_flight_jobs
# ---------------------------------------------------------------------------


def _ctx_for_recovery(repo: InMemoryJobRepository) -> AppContext:
    ctx = AppContext(
        repository=repo,
        settings=MagicMock(),
        prep_workflow=MagicMock(),
        retry_workflow=MagicMock(),
        job_queue=MagicMock(),
    )
    ctx.job_queue.put = AsyncMock()
    from src.agents.dispatcher import WorkflowDispatcher
    ctx.workflow_dispatcher = WorkflowDispatcher(ctx)
    return ctx


class TestRecovery:
    async def test_recovers_linkedin_job_to_queue(self):
        repo = InMemoryJobRepository()
        await repo.initialize()
        await repo.create(_record("lnk-1", status=BusinessState.QUEUED, source="linkedin"))

        ctx = _ctx_for_recovery(repo)
        report = await recover_in_flight_jobs(ctx)

        assert report.recovered == 1
        assert report.exhausted == 0
        ctx.job_queue.put.assert_awaited()

        row = await repo.get("lnk-1")
        assert row is not None
        assert row.recovery_attempts == 1
        assert row.last_recovery_attempt_at is not None

    async def test_cap_exhausted_marks_failed(self):
        repo = InMemoryJobRepository()
        await repo.initialize()
        await repo.create(
            _record(
                "stuck",
                status=BusinessState.PROCESSING,
                source="linkedin",
                recovery_attempts=MAX_RECOVERY_ATTEMPTS,
            )
        )

        ctx = _ctx_for_recovery(repo)
        report = await recover_in_flight_jobs(ctx)

        assert report.exhausted == 1
        assert report.recovered == 0
        ctx.job_queue.put.assert_not_called()

        row = await repo.get("stuck")
        assert row is not None
        assert row.status == BusinessState.FAILED
        assert row.error_message is not None
        assert "recovery_attempts" in row.error_message

    async def test_no_op_when_no_in_flight_rows(self):
        repo = InMemoryJobRepository()
        await repo.initialize()
        await repo.create(_record("done", status=BusinessState.COMPLETED))

        ctx = _ctx_for_recovery(repo)
        report = await recover_in_flight_jobs(ctx)

        assert report.recovered == 0
        assert report.exhausted == 0
        assert report.skipped == 0


# ---------------------------------------------------------------------------
# HITLProcessor.get_pending with states filter
# ---------------------------------------------------------------------------


class TestPendingWithStates:
    async def test_default_returns_pending_only(self):
        repo = InMemoryJobRepository()
        await repo.initialize()
        await repo.create(_record("p", status=BusinessState.PENDING, user_id="u1"))
        await repo.create(_record("q", status=BusinessState.QUEUED, user_id="u1"))

        ctx = _ctx_for_recovery(repo)
        processor = HITLProcessor(ctx)

        result = await processor.get_pending("u1")
        ids = sorted(r.job_id for r in result)
        assert ids == ["p"]

    async def test_explicit_states_includes_in_flight(self):
        repo = InMemoryJobRepository()
        await repo.initialize()
        await repo.create(_record("p", status=BusinessState.PENDING, user_id="u1"))
        await repo.create(_record("q", status=BusinessState.QUEUED, user_id="u1"))
        await repo.create(_record("pp", status=BusinessState.PROCESSING, user_id="u1"))
        await repo.create(_record("other", status=BusinessState.QUEUED, user_id="u2"))

        ctx = _ctx_for_recovery(repo)
        processor = HITLProcessor(ctx)

        result = await processor.get_pending(
            "u1",
            states=[
                BusinessState.PENDING,
                BusinessState.QUEUED,
                BusinessState.PROCESSING,
            ],
        )
        ids = sorted(r.job_id for r in result)
        assert ids == ["p", "pp", "q"]
