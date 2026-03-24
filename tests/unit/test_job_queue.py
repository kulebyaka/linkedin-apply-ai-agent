"""Tests for async job queue, LinkedInJobAdapter, and queue consumer."""

import asyncio
from unittest.mock import MagicMock

import pytest

from src.models.job import ScrapedJob
from src.services.job_queue import JobQueue, process_queue
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
        """Create a mock compiled workflow."""
        wf = MagicMock()
        wf.stream.return_value = [{"step": "done"}]
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
            q, workflow=wf, master_cv_loader=cv_loader, delay_between_jobs=0, stop_event=stop
        )

        assert count == 2
        assert wf.stream.call_count == 2

        first_call_state = wf.stream.call_args_list[0][0][0]
        assert first_call_state["job_id"] == "a"
        second_call_state = wf.stream.call_args_list[1][0][0]
        assert second_call_state["job_id"] == "b"

    async def test_handles_workflow_failure_gracefully(self):
        q = JobQueue()
        await q.put(_job("fail"))
        await q.put(_job("ok"))

        stop = asyncio.Event()
        stop.set()

        wf = MagicMock()
        call_count = 0

        def side_effect(state, config=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("LLM error")
            return [{"step": "done"}]

        wf.stream.side_effect = side_effect
        cv_loader = lambda: {"contact": {"full_name": "Test"}}

        count = await process_queue(
            q, workflow=wf, master_cv_loader=cv_loader, delay_between_jobs=0, stop_event=stop
        )

        assert count == 1

    async def test_empty_queue_with_stop_event(self):
        q = JobQueue()
        stop = asyncio.Event()
        stop.set()

        wf = MagicMock()
        cv_loader = lambda: {}

        count = await process_queue(
            q, workflow=wf, master_cv_loader=cv_loader, delay_between_jobs=0, stop_event=stop
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
            q, workflow=wf, master_cv_loader=cv_loader, delay_between_jobs=0, stop_event=stop
        )

        state = wf.stream.call_args[0][0]
        assert state["source"] == "linkedin"
        assert state["mode"] == "full"
        assert state["master_cv"] == master
        assert state["current_step"] == "queued"
        # raw_input should be a dict (model_dump output)
        assert isinstance(state["raw_input"], dict)
        assert state["raw_input"]["job_id"] == "x"
