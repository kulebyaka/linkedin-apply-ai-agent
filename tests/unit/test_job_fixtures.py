"""Unit tests for job fixture record & replay service."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.models.job import ScrapedJob
from src.services.job_fixtures import (
    enqueue_from_fixtures,
    load_scraped_jobs,
    save_scraped_jobs,
)
from src.services.job_queue import JobQueue


def _make_job(job_id: str = "test-001", title: str = "Engineer") -> ScrapedJob:
    return ScrapedJob(
        job_id=job_id,
        title=title,
        company="TestCo",
        location="Remote",
        url=f"https://linkedin.com/jobs/view/{job_id}",
        description="A test job description.",
    )


class TestSaveScrapedJobs:
    def test_saves_jobs_to_json(self, tmp_path):
        path = tmp_path / "jobs.json"
        jobs = [_make_job("j1"), _make_job("j2")]

        count = save_scraped_jobs(jobs, path)

        assert count == 2
        assert path.exists()
        data = json.loads(path.read_text())
        assert len(data) == 2
        assert data[0]["job_id"] == "j1"
        assert data[1]["job_id"] == "j2"

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "jobs.json"
        save_scraped_jobs([_make_job()], path)
        assert path.exists()

    def test_overwrites_existing_file(self, tmp_path):
        path = tmp_path / "jobs.json"
        save_scraped_jobs([_make_job("old")], path)
        save_scraped_jobs([_make_job("new")], path)

        data = json.loads(path.read_text())
        assert len(data) == 1
        assert data[0]["job_id"] == "new"

    def test_saves_empty_list(self, tmp_path):
        path = tmp_path / "jobs.json"
        count = save_scraped_jobs([], path)
        assert count == 0
        assert json.loads(path.read_text()) == []


class TestLoadScrapedJobs:
    def test_loads_jobs_from_file(self, tmp_path):
        path = tmp_path / "jobs.json"
        save_scraped_jobs([_make_job("j1"), _make_job("j2")], path)

        jobs = load_scraped_jobs(path)
        assert len(jobs) == 2
        assert jobs[0].job_id == "j1"

    def test_respects_limit(self, tmp_path):
        path = tmp_path / "jobs.json"
        save_scraped_jobs([_make_job(f"j{i}") for i in range(5)], path)

        jobs = load_scraped_jobs(path, limit=2)
        assert len(jobs) == 2

    def test_zero_limit_means_no_limit(self, tmp_path):
        path = tmp_path / "jobs.json"
        save_scraped_jobs([_make_job(f"j{i}") for i in range(5)], path)

        jobs = load_scraped_jobs(path, limit=0)
        assert len(jobs) == 5

    def test_missing_file_returns_empty(self, tmp_path):
        jobs = load_scraped_jobs(tmp_path / "nonexistent.json")
        assert jobs == []

    def test_malformed_json_returns_empty(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not valid json {{{")
        jobs = load_scraped_jobs(path)
        assert jobs == []

    def test_non_array_json_returns_empty(self, tmp_path):
        path = tmp_path / "obj.json"
        path.write_text('{"not": "an array"}')
        jobs = load_scraped_jobs(path)
        assert jobs == []

    def test_skips_invalid_entries(self, tmp_path):
        path = tmp_path / "mixed.json"
        data = [
            _make_job("valid").model_dump(mode="json"),
            {"invalid": "entry"},  # missing required fields
            _make_job("also-valid").model_dump(mode="json"),
        ]
        path.write_text(json.dumps(data))

        jobs = load_scraped_jobs(path)
        assert len(jobs) == 2
        assert jobs[0].job_id == "valid"
        assert jobs[1].job_id == "also-valid"

    def test_roundtrip_preserves_data(self, tmp_path):
        path = tmp_path / "jobs.json"
        original = _make_job("rt-1")
        original.salary_range = "$100k-$150k"
        original.experience_level = "Senior"
        save_scraped_jobs([original], path)

        loaded = load_scraped_jobs(path)
        assert len(loaded) == 1
        assert loaded[0].job_id == original.job_id
        assert loaded[0].salary_range == original.salary_range
        assert loaded[0].experience_level == original.experience_level


@pytest.mark.asyncio
class TestEnqueueFromFixtures:
    @pytest.fixture
    def queue(self):
        return JobQueue(max_size=50)

    async def test_enqueues_all_jobs(self, tmp_path, queue):
        path = tmp_path / "jobs.json"
        save_scraped_jobs([_make_job("e1"), _make_job("e2")], path)

        result = await enqueue_from_fixtures(path, queue)

        assert result["enqueued"] == 2
        assert result["skipped"] == 0
        assert result["total_in_file"] == 2
        assert queue.size() == 2

    async def test_deduplicates_against_repository(self, tmp_path, queue):
        path = tmp_path / "jobs.json"
        save_scraped_jobs([_make_job("dup"), _make_job("new")], path)

        repo = AsyncMock()
        # First job exists in repo, second doesn't
        repo.get = AsyncMock(side_effect=[AsyncMock(status="pending"), None])

        result = await enqueue_from_fixtures(path, queue, repository=repo)

        assert result["enqueued"] == 1
        assert result["skipped"] == 1
        assert queue.size() == 1

    async def test_respects_limit(self, tmp_path, queue):
        path = tmp_path / "jobs.json"
        save_scraped_jobs([_make_job(f"l{i}") for i in range(5)], path)

        result = await enqueue_from_fixtures(path, queue, limit=2)

        assert result["enqueued"] == 2
        assert result["total_in_file"] == 2
        assert queue.size() == 2

    async def test_missing_file_returns_zeros(self, tmp_path, queue):
        result = await enqueue_from_fixtures(tmp_path / "nope.json", queue)

        assert result == {"enqueued": 0, "skipped": 0, "total_in_file": 0}
        assert queue.size() == 0

    async def test_dedup_failure_enqueues_anyway(self, tmp_path, queue):
        path = tmp_path / "jobs.json"
        save_scraped_jobs([_make_job("fail-dedup")], path)

        repo = AsyncMock()
        repo.get = AsyncMock(side_effect=Exception("DB error"))

        result = await enqueue_from_fixtures(path, queue, repository=repo)

        assert result["enqueued"] == 1
        assert queue.size() == 1
