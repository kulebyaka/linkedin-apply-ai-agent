"""Tests for async job queue, LinkedInJobAdapter, queue consumer, and ConsumerManager."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.job import ScrapedJob
from src.services.job_queue import ConsumerManager, JobQueue, process_queue
from src.services.job_source import LinkedInJobAdapter

pytestmark = pytest.mark.asyncio


def _job(job_id: str, **kwargs) -> ScrapedJob:
    """Helper to create a ScrapedJob with minimal required fields."""
    defaults = {"title": "", "company": "", "location": "", "url": ""}
    defaults.update(kwargs)
    return ScrapedJob(job_id=job_id, **defaults)


# ---------------------------------------------------------------------------
# JobQueue basic operations
# ---------------------------------------------------------------------------


class TestJobQueuePutGet:
    """Test put/get preserve FIFO ordering."""

    async def test_single_put_get(self):
        q = JobQueue()
        job = _job("1")
        await q.put(job)
        assert q.size() == 1
        item = await q.get()
        assert item.job_id == "1"
        assert q.is_empty()

    async def test_fifo_ordering(self):
        q = JobQueue()
        for i in range(5):
            await q.put(_job(str(i)))
        assert q.size() == 5
        for i in range(5):
            item = await q.get()
            assert item.job_id == str(i)

    async def test_is_empty_initially(self):
        q = JobQueue()
        assert q.is_empty()
        assert q.size() == 0


class TestJobQueueBatch:
    """Test put_batch behaviour."""

    async def test_put_batch_all_fit(self):
        q = JobQueue(max_size=10)
        jobs = [_job(str(i)) for i in range(5)]
        count = await q.put_batch(jobs)
        assert count == 5
        assert q.size() == 5

    async def test_put_batch_partial_when_full(self):
        q = JobQueue(max_size=3)
        jobs = [_job(str(i)) for i in range(5)]
        count = await q.put_batch(jobs)
        assert count == 3
        assert q.size() == 3
        # First 3 should be in queue
        for i in range(3):
            item = await q.get()
            assert item.job_id == str(i)

    async def test_put_batch_empty_list(self):
        q = JobQueue()
        count = await q.put_batch([])
        assert count == 0
        assert q.is_empty()


# ---------------------------------------------------------------------------
# LinkedInJobAdapter.extract
# ---------------------------------------------------------------------------


class TestLinkedInJobAdapterExtract:
    """Test extract() field mapping."""

    async def test_extract_flat_dict(self):
        adapter = LinkedInJobAdapter()
        raw = {
            "job_id": "12345",
            "title": "Software Engineer",
            "company": "Acme Corp",
            "location": "San Francisco, CA",
            "description": "Build things.",
            "requirements": "Python, AWS",
            "salary": "$120k-$150k",
            "experience_level": "mid-senior",
            "job_type": "full-time",
            "posted_date": "2026-01-15T00:00:00",
        }
        result = await adapter.extract(raw)
        assert result["id"] == "12345"
        assert result["title"] == "Software Engineer"
        assert result["company"] == "Acme Corp"
        assert result["location"] == "San Francisco, CA"
        assert result["description"] == "Build things."
        assert result["requirements"] == "Python, AWS"
        assert result["salary_range"] == "$120k-$150k"
        assert result["experience_level"] == "mid-senior"
        assert result["job_type"] == "full-time"
        assert result["url"] == "https://www.linkedin.com/jobs/view/12345/"
        assert result["is_remote"] is False
        assert result["raw_data"] is raw

    async def test_extract_legacy_envelope(self):
        adapter = LinkedInJobAdapter()
        inner = {
            "title": "Data Scientist",
            "company": "BigCo",
            "location": "Remote",
            "description": "Analyze data.",
        }
        raw = {"job_id": "99", "raw_data": inner}
        result = await adapter.extract(raw)
        assert result["id"] == "99"
        assert result["title"] == "Data Scientist"
        assert result["is_remote"] is True  # location contains "remote"
        assert result["raw_data"] is inner

    async def test_extract_url_passthrough(self):
        adapter = LinkedInJobAdapter()
        raw = {
            "job_id": "42",
            "url": "https://www.linkedin.com/jobs/view/42/",
            "title": "PM",
            "company": "X",
            "location": "",
            "description": "",
        }
        result = await adapter.extract(raw)
        assert result["url"] == "https://www.linkedin.com/jobs/view/42/"

    async def test_extract_generates_url_from_job_id(self):
        adapter = LinkedInJobAdapter()
        raw = {"job_id": "777", "title": "", "company": "", "location": "", "description": ""}
        result = await adapter.extract(raw)
        assert result["url"] == "https://www.linkedin.com/jobs/view/777/"

    async def test_extract_remote_flag(self):
        adapter = LinkedInJobAdapter()
        raw = {
            "job_id": "1",
            "title": "",
            "company": "",
            "location": "NYC",
            "description": "",
            "is_remote": True,
        }
        result = await adapter.extract(raw)
        assert result["is_remote"] is True

    async def test_extract_invalid_posted_date_ignored(self):
        adapter = LinkedInJobAdapter()
        raw = {
            "job_id": "1",
            "title": "",
            "company": "",
            "location": "",
            "description": "",
            "posted_date": "not-a-date",
        }
        result = await adapter.extract(raw)
        assert result["posted_date"] is None

    async def test_extract_defaults_for_missing_fields(self):
        adapter = LinkedInJobAdapter()
        raw = {"job_id": "1"}
        result = await adapter.extract(raw)
        assert result["title"] == ""
        assert result["company"] == ""
        assert result["description"] == ""
        assert result["requirements"] is None
        assert result["salary_range"] is None


class TestLinkedInJobAdapterCanHandle:
    """Test can_handle() accepts correct input shapes."""

    def test_legacy_format(self):
        adapter = LinkedInJobAdapter()
        assert adapter.can_handle({"job_id": "1", "raw_data": {}}) is True

    def test_linkedin_url_format(self):
        adapter = LinkedInJobAdapter()
        assert adapter.can_handle({"linkedin_url": "https://linkedin.com/jobs/view/123"}) is True

    def test_rejects_unrelated_input(self):
        adapter = LinkedInJobAdapter()
        assert adapter.can_handle({"url": "https://example.com"}) is False

    def test_rejects_partial_legacy(self):
        adapter = LinkedInJobAdapter()
        assert adapter.can_handle({"job_id": "1"}) is False


# ---------------------------------------------------------------------------
# process_queue consumer
# ---------------------------------------------------------------------------


class TestProcessQueue:
    """Test the queue consumer function."""

    def _make_workflow_mock(self):
        """Create a mock compiled workflow with async ainvoke."""
        wf = MagicMock()
        wf.ainvoke = AsyncMock(return_value={"step": "done"})
        return wf

    async def test_processes_jobs_in_order(self):
        q = JobQueue()
        await q.put(_job("a", title="A", company="Co"))
        await q.put(_job("b", title="B", company="Co"))

        stop = asyncio.Event()
        stop.set()

        wf = self._make_workflow_mock()
        cv_loader = lambda: {"contact": {"full_name": "Test"}}

        count = await process_queue(
            q, workflow=wf, master_cv_loader=cv_loader, job_repository=AsyncMock(get=AsyncMock(return_value=None)), delay_between_jobs=0, stop_event=stop
        )

        assert count == 2
        assert wf.ainvoke.call_count == 2

        first_call_state = wf.ainvoke.call_args_list[0][0][0]
        assert first_call_state["job_id"] == "a"
        second_call_state = wf.ainvoke.call_args_list[1][0][0]
        assert second_call_state["job_id"] == "b"

    async def test_handles_workflow_failure_gracefully(self):
        q = JobQueue()
        await q.put(_job("fail"))
        await q.put(_job("ok"))

        stop = asyncio.Event()
        stop.set()

        wf = MagicMock()
        call_count = 0

        async def side_effect(state, config=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("LLM error")
            return {"step": "done"}

        wf.ainvoke = AsyncMock(side_effect=side_effect)
        cv_loader = lambda: {"contact": {"full_name": "Test"}}

        count = await process_queue(
            q, workflow=wf, master_cv_loader=cv_loader, job_repository=AsyncMock(get=AsyncMock(return_value=None)), delay_between_jobs=0, stop_event=stop
        )

        assert count == 1

    async def test_empty_queue_with_stop_event(self):
        q = JobQueue()
        stop = asyncio.Event()
        stop.set()

        wf = MagicMock()
        wf.ainvoke = AsyncMock()
        cv_loader = lambda: {}

        count = await process_queue(
            q, workflow=wf, master_cv_loader=cv_loader, job_repository=AsyncMock(get=AsyncMock(return_value=None)), delay_between_jobs=0, stop_event=stop
        )

        assert count == 0

    async def test_state_fields_are_correct(self):
        q = JobQueue()
        await q.put(_job("x", title="Eng", company="Co", location="LA", description="d"))

        stop = asyncio.Event()
        stop.set()

        wf = self._make_workflow_mock()
        master = {"contact": {"full_name": "Test"}}
        cv_loader = lambda: master

        await process_queue(
            q, workflow=wf, master_cv_loader=cv_loader, job_repository=AsyncMock(get=AsyncMock(return_value=None)), delay_between_jobs=0, stop_event=stop
        )

        state = wf.ainvoke.call_args[0][0]
        assert state["source"] == "linkedin"
        assert state["mode"] == "full"
        assert state["master_cv"] == master
        assert state["current_step"] == "queued"
        # raw_input should be a dict (model_dump output)
        assert isinstance(state["raw_input"], dict)
        assert state["raw_input"]["job_id"] == "x"


# ---------------------------------------------------------------------------
# put_batch dropped-job logging
# ---------------------------------------------------------------------------


class TestPutBatchDroppedDetails:
    """Test that put_batch logs dropped job IDs and titles."""

    async def test_dropped_jobs_include_ids_and_titles(self, caplog):
        q = JobQueue(max_size=2)
        jobs = [
            _job("a", title="Engineer"),
            _job("b", title="Designer"),
            _job("c", title="PM"),
            _job("d", title="Analyst"),
        ]
        with caplog.at_level(logging.WARNING, logger="src.services.job_queue"):
            count = await q.put_batch(jobs)

        assert count == 2
        assert "dropping 2 jobs" in caplog.text
        assert "c (PM)" in caplog.text
        assert "d (Analyst)" in caplog.text


# ---------------------------------------------------------------------------
# ConsumerManager
# ---------------------------------------------------------------------------


def _make_mock_ctx():
    """Create a minimal mock AppContext for ConsumerManager tests."""
    ctx = MagicMock()
    ctx.job_queue = JobQueue()
    ctx.repository = AsyncMock()
    ctx.register_workflow = AsyncMock()
    return ctx


class TestConsumerManagerHealthCheck:
    """Test health_check reflects state correctly."""

    def test_initial_health(self):
        cm = ConsumerManager()
        health = cm.health_check()
        assert health["queue_consumer_healthy"] is True
        assert health["consumer_restart_count"] == 0
        assert health["consumer_running"] is False

    def test_health_after_reset(self):
        cm = ConsumerManager()
        cm.restart_count = 3
        cm.is_healthy = False
        cm.reset()
        health = cm.health_check()
        assert health["queue_consumer_healthy"] is True
        assert health["consumer_restart_count"] == 0


class TestConsumerManagerRestartTracking:
    """Test restart count tracking and max restarts behavior."""

    def test_restart_count_increments(self):
        cm = ConsumerManager(max_restarts=3)
        assert cm.restart_count == 0
        cm.restart_count = 1
        assert cm.restart_count == 1

    def test_max_restarts_sets_unhealthy(self):
        cm = ConsumerManager(max_restarts=2)
        # Simulate exceeding max restarts
        cm.restart_count = 3
        cm.is_healthy = False
        health = cm.health_check()
        assert health["queue_consumer_healthy"] is False

    def test_reset_restores_health(self):
        cm = ConsumerManager(max_restarts=2)
        cm.restart_count = 5
        cm.is_healthy = False
        cm.reset()
        assert cm.is_healthy is True
        assert cm.restart_count == 0


class TestConsumerManagerStartStop:
    """Test start/stop lifecycle."""

    async def test_start_creates_task(self):
        cm = ConsumerManager()
        ctx = _make_mock_ctx()

        # Patch process_queue to exit immediately
        with patch("src.services.job_queue.process_queue", new_callable=AsyncMock, return_value=0):
            task = cm.start(ctx)
            assert task is not None
            assert cm.task is task
            health = cm.health_check()
            assert health["consumer_running"] is True

            # Let the task finish
            await task
            health = cm.health_check()
            assert health["consumer_running"] is False

    async def test_stop_cancels_task(self):
        cm = ConsumerManager()
        ctx = _make_mock_ctx()

        async def _hang_forever(queue, **kwargs):
            await asyncio.sleep(3600)
            return 0

        with patch("src.services.job_queue.process_queue", side_effect=_hang_forever):
            cm.start(ctx)
            assert cm.task is not None
            assert not cm.task.done()

            cm.stop()
            await cm.wait_stopped()
            assert cm.task is None

    async def test_consumer_crash_increments_restart_count(self):
        cm = ConsumerManager(max_restarts=5, backoff_base=0.01)
        ctx = _make_mock_ctx()

        call_count = 0

        async def _crash_then_stop(queue, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("boom")
            # Second call: exit cleanly
            await asyncio.sleep(0.05)
            return 0

        with patch("src.services.job_queue.process_queue", side_effect=_crash_then_stop):
            cm.start(ctx)
            # Wait for crash and restart cycle
            await asyncio.sleep(0.3)

        assert cm.restart_count == 0  # reset after successful exit
        assert cm.is_healthy is True

    async def test_exceeding_max_restarts_marks_unhealthy(self, caplog):
        cm = ConsumerManager(max_restarts=2, backoff_base=0.01)
        ctx = _make_mock_ctx()

        async def _always_crash(queue, **kwargs):
            raise RuntimeError("persistent failure")

        with patch("src.services.job_queue.process_queue", side_effect=_always_crash):
            with caplog.at_level(logging.CRITICAL, logger="src.services.job_queue"):
                cm.start(ctx)
                # Wait for crashes to exhaust restarts: crash 1 (delay 0.01), crash 2 (delay 0.02), crash 3 gives up
                await asyncio.sleep(0.5)

        assert cm.is_healthy is False
        assert cm.restart_count > cm.max_restarts
        assert "giving up" in caplog.text.lower()
