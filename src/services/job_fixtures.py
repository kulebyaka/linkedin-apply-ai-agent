"""Record and replay scraped LinkedIn jobs from a local JSON fixture file.

Enables fast HITL testing and demos without live LinkedIn scraping.
Recording happens automatically after each scrape; replay loads jobs from
file and enqueues them into the job queue with deduplication.

Also provides LLM response caching: when fixture replay mode is active,
LLM outputs (tailored CV JSON) are cached per job_id so repeated runs
skip expensive LLM calls entirely.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.models.job import ScrapedJob
from src.services.job_queue import JobQueue

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM response cache
# ---------------------------------------------------------------------------

_DEFAULT_LLM_CACHE_PATH = Path("./data/jobs/llm_response_cache.json")


def _load_llm_cache(path: Path) -> dict[str, Any]:
    """Load LLM response cache from disk."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
        if isinstance(raw, dict):
            return raw
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read LLM cache %s: %s", path, exc)
    return {}


def _save_llm_cache(cache: dict[str, Any], path: Path) -> None:
    """Persist LLM response cache to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, default=str))


def get_cached_llm_response(job_id: str, path: Path | None = None) -> dict[str, Any] | None:
    """Return cached tailored CV JSON for *job_id*, or ``None`` on miss."""
    cache = _load_llm_cache(path or _DEFAULT_LLM_CACHE_PATH)
    hit = cache.get(job_id)
    if hit is not None:
        logger.info("LLM cache HIT for job %s", job_id)
    return hit


def save_llm_response(job_id: str, tailored_cv_json: dict[str, Any], path: Path | None = None) -> None:
    """Persist a tailored CV JSON to the LLM response cache."""
    p = path or _DEFAULT_LLM_CACHE_PATH
    cache = _load_llm_cache(p)
    cache[job_id] = tailored_cv_json
    _save_llm_cache(cache, p)
    logger.info("LLM cache SAVE for job %s", job_id)


def save_scraped_jobs(jobs: list[ScrapedJob], path: str | Path) -> int:
    """Serialize a list of ScrapedJob to a JSON file (overwrites).

    Returns the number of jobs saved.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = [job.model_dump(mode="json") for job in jobs]
    path.write_text(json.dumps(data, indent=2, default=str))
    logger.info("Saved %d scraped jobs to %s", len(data), path)
    return len(data)


def load_scraped_jobs(path: str | Path, limit: int = 0) -> list[ScrapedJob]:
    """Load ScrapedJob list from a JSON fixture file.

    Parameters
    ----------
    path:
        Path to the JSON file containing serialized ScrapedJob dicts.
    limit:
        Maximum number of jobs to load. 0 means no limit.

    Returns an empty list (with a warning) if the file is missing or malformed.
    """
    path = Path(path)
    if not path.exists():
        logger.warning("Fixture file not found: %s", path)
        return []

    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read fixture file %s: %s", path, exc)
        return []

    if not isinstance(raw, list):
        logger.error("Fixture file %s does not contain a JSON array", path)
        return []

    jobs: list[ScrapedJob] = []
    for i, entry in enumerate(raw):
        try:
            jobs.append(ScrapedJob.model_validate(entry))
        except Exception as exc:
            logger.warning("Skipping invalid entry #%d in %s: %s", i, path, exc)

    if limit > 0:
        jobs = jobs[:limit]

    logger.info("Loaded %d scraped jobs from %s", len(jobs), path)
    return jobs


async def enqueue_from_fixtures(
    path: str | Path,
    queue: JobQueue,
    repository=None,
    limit: int = 0,
) -> dict:
    """Load jobs from fixture file, deduplicate, and enqueue.

    Parameters
    ----------
    path:
        Path to the JSON fixture file.
    queue:
        JobQueue to enqueue into.
    repository:
        Optional JobRepository for deduplication (skip already-processed jobs).
    limit:
        Max jobs to load from file. 0 means no limit.

    Returns a dict with enqueued/skipped/total_in_file counts.
    """
    jobs = load_scraped_jobs(path, limit=limit)
    total_in_file = len(jobs)

    if not jobs:
        return {"enqueued": 0, "skipped": 0, "total_in_file": 0}

    # Deduplicate against repository
    skipped = 0
    to_enqueue: list[ScrapedJob] = []
    for job in jobs:
        if repository is not None:
            try:
                existing = await repository.get(job.job_id)
                if existing is not None:
                    logger.debug("Fixture dedup: skipping job %s (already in repo)", job.job_id)
                    skipped += 1
                    continue
            except Exception:
                logger.warning("Dedup check failed for fixture job %s, enqueuing anyway", job.job_id)
        to_enqueue.append(job)

    enqueued = await queue.put_batch(to_enqueue)

    logger.info(
        "Fixture replay: enqueued=%d, skipped=%d, total_in_file=%d",
        enqueued, skipped, total_in_file,
    )
    return {"enqueued": enqueued, "skipped": skipped, "total_in_file": total_in_file}
