"""One-shot cleanup for JobRecords stuck with empty/short scraped descriptions.

After a scraper regression that persisted broken rows, those rows became
dedup-locked: cross-cycle dedup in job_queue.py would skip them forever even
once the scraper was fixed. This script identifies the backlog and either:

  --mode rescrape  (default): transitions the row to SCRAPE_FAILED with
                              scrape_attempts=0 so the scheduler's next tick
                              naturally re-processes it.
  --mode delete:              cascade-deletes the row (via delete_for_user),
                              dropping CV attempts and PDF files.

Dry-run by default. Pass --apply to commit changes.

Usage:
    python -m scripts.cleanup_empty_description_jobs                  # dry-run, rescrape
    python -m scripts.cleanup_empty_description_jobs --apply          # commit, rescrape
    python -m scripts.cleanup_empty_description_jobs --mode delete --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections import Counter
from datetime import datetime, timezone

from src.config.settings import get_settings
from src.models.state_machine import BusinessState
from src.services.db.job_repository import JobRepository, get_repository

logger = logging.getLogger(__name__)


# Statuses whose rows we consider for cleanup. SCRAPE_FAILED is already
# retry-eligible by design, so we don't sweep it. FILTERED_OUT is intentionally
# excluded — its empty description is expected (no detail-page fetch).
CLEANABLE_STATUSES = (
    BusinessState.PENDING_REVIEW,
    BusinessState.CV_READY,
    BusinessState.FAILED,
    BusinessState.APPROVED,
    BusinessState.DECLINED,
    BusinessState.APPLIED,
)


def _is_short_description(record_posting: dict | None, min_chars: int) -> bool:
    description = ""
    if record_posting and isinstance(record_posting, dict):
        description = (record_posting.get("description") or "").strip()
    return len(description) < min_chars


async def _find_candidates(repo: JobRepository, min_chars: int) -> list:
    """Cross-user sweep of cleanable rows with short descriptions.

    The repo's per-user query methods aren't suitable for an admin sweep, so we
    query the underlying table directly (SQLite only — InMemoryJobRepository
    isn't a meaningful target for backlog cleanup).
    """
    from src.services.db.tables import Job

    candidates = []
    for status in CLEANABLE_STATUSES:
        rows = (
            await Job.select()
            .where(Job.status == status.value)
            .run()
        )
        for row in rows:
            record = repo._row_to_job_record(row)  # type: ignore[attr-defined]
            if _is_short_description(record.job_posting, min_chars):
                candidates.append(record)
    return candidates


async def _apply_rescrape(repo: JobRepository, record) -> None:
    now = datetime.now(tz=timezone.utc)
    await repo.update(
        record.job_id,
        {
            "status": BusinessState.SCRAPE_FAILED,
            "scrape_attempts": 0,
            "last_scrape_error": "Backfill: empty description from prior scraper bug",
            "last_scrape_attempt_at": now,
        },
    )


async def _apply_delete(repo: JobRepository, record) -> None:
    await repo.delete_for_user(record.job_id, record.user_id)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("rescrape", "delete"),
        default="rescrape",
        help="What to do with each matched row (default: rescrape).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit changes. Without this flag, runs as dry-run and only reports counts.",
    )
    parser.add_argument(
        "--min-chars",
        type=int,
        default=None,
        help="Override the description length threshold (default: settings.scraper_min_description_chars).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    settings = get_settings()
    min_chars = args.min_chars if args.min_chars is not None else settings.scraper_min_description_chars
    logger.info("Threshold: description must have at least %d chars", min_chars)
    logger.info("Mode: %s | Apply: %s", args.mode, args.apply)

    repo = get_repository(repo_type=settings.repo_type, db_path=settings.db_path)
    await repo.initialize()

    try:
        candidates = await _find_candidates(repo, min_chars)
        per_status: Counter = Counter(c.status for c in candidates)
        per_user: Counter = Counter(c.user_id for c in candidates)

        logger.info("Found %d candidates", len(candidates))
        for status, count in sorted(per_status.items()):
            logger.info("  by status: %s = %d", status, count)
        for user_id, count in sorted(per_user.items()):
            logger.info("  by user_id: %s = %d", user_id or "<empty>", count)

        if not args.apply:
            logger.info("Dry-run — no changes written. Re-run with --apply to commit.")
            return 0

        action = _apply_delete if args.mode == "delete" else _apply_rescrape
        succeeded = 0
        failed = 0
        for record in candidates:
            try:
                await action(repo, record)
                succeeded += 1
            except Exception as exc:
                failed += 1
                logger.error("Failed for job %s (user=%s): %s", record.job_id, record.user_id, exc)

        logger.info("Done. Succeeded: %d, Failed: %d", succeeded, failed)
        return 0 if failed == 0 else 1
    finally:
        await repo.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
