"""Tests for workflow timeout handling in process_queue.

Verifies that:
1. A workflow exceeding workflow_timeout_seconds is cancelled and the job is
   marked failed with a descriptive error_message.
2. Non-timeout exceptions populate error_message with the exception string.
3. The error_message is truncated to ~500 chars for long messages.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.job import ScrapedJob
from src.models.state_machine import BusinessState
from src.models.unified import JobRecord
from src.services.jobs.job_queue import JobQueue, process_queue

pytestmark = pytest.mark.asyncio


def _job(job_id: str, **kwargs) -> ScrapedJob:
    defaults = {"title": "", "company": "", "location": "", "url": ""}
    defaults.update(kwargs)
    return ScrapedJob(job_id=job_id, **defaults)


class _RecordingRepo:
    """In-memory test double recording create()/update() calls."""

    def __init__(self):
        self.records: dict[str, JobRecord] = {}
        self.create_calls: list[JobRecord] = []
        self.update_calls: list[tuple[str, dict]] = []

    async def get(self, job_id: str):
        return self.records.get(job_id)

    async def create(self, record: JobRecord):
        self.records[record.job_id] = record
        self.create_calls.append(record)
        return record

    async def update(self, job_id: str, patch: dict):
        self.update_calls.append((job_id, dict(patch)))
        existing = self.records.get(job_id)
        if existing is None:
            return None
        updated = existing.model_copy(update=patch)
        self.records[job_id] = updated
        return updated


async def test_workflow_timeout_marks_job_failed_with_error_message():
    """A workflow exceeding the timeout is cancelled and the job is recorded as failed."""
    q = JobQueue()
    await q.put(_job("slow"))

    stop = asyncio.Event()
    stop.set()

    async def slow_workflow(state, config=None):
        # Sleep much longer than the timeout to force asyncio.wait_for cancellation
        await asyncio.sleep(10)
        return {"step": "done"}

    wf = MagicMock()
    wf.ainvoke = AsyncMock(side_effect=slow_workflow)

    repo = _RecordingRepo()

    def cv_loader():
        return {"contact": {"full_name": "Test"}}

    count = await process_queue(
        q,
        workflow=wf,
        master_cv_loader=cv_loader,
        job_repository=repo,
        delay_between_jobs=0,
        stop_event=stop,
        workflow_timeout_seconds=0.1,
    )

    # Workflow timed out — no successful processing
    assert count == 0
    # A failure record was created (job did not exist beforehand)
    assert len(repo.create_calls) == 1
    record = repo.create_calls[0]
    assert record.status == BusinessState.FAILED
    assert record.error_message is not None
    assert "timed out" in record.error_message.lower()


async def test_workflow_exception_populates_error_message():
    """A non-timeout workflow failure surfaces the exception text in error_message."""
    q = JobQueue()
    await q.put(_job("boom"))

    stop = asyncio.Event()
    stop.set()

    wf = MagicMock()
    wf.ainvoke = AsyncMock(side_effect=RuntimeError("LLM key invalid"))

    repo = _RecordingRepo()

    def cv_loader():
        return {"contact": {"full_name": "Test"}}

    await process_queue(
        q,
        workflow=wf,
        master_cv_loader=cv_loader,
        job_repository=repo,
        delay_between_jobs=0,
        stop_event=stop,
        workflow_timeout_seconds=5.0,
    )

    assert len(repo.create_calls) == 1
    record = repo.create_calls[0]
    assert record.status == BusinessState.FAILED
    assert record.error_message == "LLM key invalid"


async def test_workflow_long_exception_truncated():
    """Very long exception messages are truncated to ~500 chars to avoid bloating storage."""
    q = JobQueue()
    await q.put(_job("longerr"))

    stop = asyncio.Event()
    stop.set()

    long_msg = "x" * 2000
    wf = MagicMock()
    wf.ainvoke = AsyncMock(side_effect=RuntimeError(long_msg))

    repo = _RecordingRepo()

    def cv_loader():
        return {"contact": {"full_name": "Test"}}

    await process_queue(
        q,
        workflow=wf,
        master_cv_loader=cv_loader,
        job_repository=repo,
        delay_between_jobs=0,
        stop_event=stop,
        workflow_timeout_seconds=5.0,
    )

    record = repo.create_calls[0]
    assert record.error_message is not None
    assert len(record.error_message) <= 500


async def test_workflow_timeout_updates_when_workflow_created_record_midflight():
    """If a workflow node persists the record before timing out, the failure
    path updates that record rather than creating a duplicate.

    Simulates the production case where extract_job_node creates the record in
    PROCESSING state, then a later node hangs and triggers the timeout.
    """
    q = JobQueue()
    await q.put(_job("midflight"))

    stop = asyncio.Event()
    stop.set()

    from datetime import datetime, timezone

    repo = _RecordingRepo()

    async def workflow_that_persists_then_hangs(state, config=None):
        # Simulate a node creating the record before the next node hangs
        now = datetime.now(tz=timezone.utc)
        await repo.create(
            JobRecord(
                job_id="midflight",
                user_id="",
                source="linkedin",
                mode="full",
                status=BusinessState.PROCESSING,
                raw_input={"job_id": "midflight"},
                created_at=now,
                updated_at=now,
            )
        )
        repo.create_calls.clear()  # only track failure-path creates
        await asyncio.sleep(10)
        return {"step": "done"}

    wf = MagicMock()
    wf.ainvoke = AsyncMock(side_effect=workflow_that_persists_then_hangs)

    def cv_loader():
        return {"contact": {"full_name": "Test"}}

    await process_queue(
        q,
        workflow=wf,
        master_cv_loader=cv_loader,
        job_repository=repo,
        delay_between_jobs=0,
        stop_event=stop,
        workflow_timeout_seconds=0.1,
    )

    # The mid-flight record was updated, not recreated
    assert len(repo.create_calls) == 0
    assert len(repo.update_calls) == 1
    job_id, patch = repo.update_calls[0]
    assert job_id == "midflight"
    assert patch["status"] == BusinessState.FAILED
    assert "timed out" in patch["error_message"].lower()
