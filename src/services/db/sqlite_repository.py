"""SQLite JobRepository implementation backed by Piccolo ORM.

Schema migrations live in `migrations.py`. Cross-user admin reporting queries
live in `sqlite_admin_queries.py` and are mixed into this class.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.models.cv_attempt import CVCompositionAttempt
from src.models.state_machine import BusinessState, validate_transition
from src.models.unified import JobRecord

from .migrations import apply_migrations
from .repository import UPDATABLE_FIELDS, JobRepository, RepositoryError, _unlink_pdfs
from .sqlite_admin_queries import SQLiteAdminQueriesMixin

logger = logging.getLogger(__name__)


class SQLiteJobRepository(SQLiteAdminQueriesMixin, JobRepository):
    """SQLite implementation of JobRepository using Piccolo ORM.

    Production-ready persistent storage. Tables are created if missing and
    pending schema migrations are applied on initialize().
    """

    def __init__(self, db_path: str = "data/jobs.db"):
        self.db_path = db_path
        self._initialized: bool = False
        self._engine = None

    # =========================================================================
    # Lifecycle Methods
    # =========================================================================

    async def initialize(self) -> None:
        """Initialize database (create tables + apply migrations)."""
        from piccolo.engine.sqlite import SQLiteEngine

        from .tables import CVAttemptTable, Job, MagicLinkTable, UserTable

        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        self._engine = SQLiteEngine(path=self.db_path)

        Job._meta._db = self._engine
        CVAttemptTable._meta._db = self._engine
        UserTable._meta._db = self._engine
        MagicLinkTable._meta._db = self._engine

        try:
            await UserTable.create_table(if_not_exists=True).run()
            await MagicLinkTable.create_table(if_not_exists=True).run()
            await Job.create_table(if_not_exists=True).run()
            await CVAttemptTable.create_table(if_not_exists=True).run()

            await apply_migrations(self._engine)

            logger.info(f"SQLite repository initialized at {self.db_path}")
            self._initialized = True
        except Exception as e:
            logger.error(f"Failed to initialize SQLite repository: {e}")
            raise RepositoryError(f"Database initialization failed: {e}") from e

    async def close(self) -> None:
        if self._engine:
            await self._engine.close_connection_pool()
            self._engine = None
        self._initialized = False
        logger.info("SQLite repository closed")

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            raise RepositoryError("Repository not initialized. Call initialize() first.")

    # =========================================================================
    # Conversion Helpers
    # =========================================================================

    def _job_record_to_row(self, job: JobRecord) -> dict:
        return {
            "job_id": job.job_id,
            "user_id": job.user_id,
            "source": job.source,
            "mode": job.mode,
            "status": job.status,
            "job_posting": job.job_posting,
            "raw_input": job.raw_input,
            "current_cv_json": job.current_cv_json,
            "current_pdf_path": job.current_pdf_path,
            "application_url": job.application_url,
            "filter_result": job.filter_result,
            "error_message": job.error_message,
            "scrape_attempts": job.scrape_attempts,
            "last_scrape_error": job.last_scrape_error,
            "last_scrape_attempt_at": job.last_scrape_attempt_at,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }

    def _parse_json_field(self, value) -> dict | None:
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return json.loads(value)
        return value

    def _normalize_datetime(self, dt) -> datetime | None:
        if dt is None:
            return None
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)
        if hasattr(dt, 'replace') and dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def _row_to_job_record(self, row: dict) -> JobRecord:
        return JobRecord(
            job_id=row["job_id"],
            user_id=row.get("user_id", ""),
            source=row["source"],
            mode=row["mode"],
            status=row["status"],
            job_posting=self._parse_json_field(row.get("job_posting")),
            raw_input=self._parse_json_field(row.get("raw_input")),
            current_cv_json=self._parse_json_field(row.get("current_cv_json")),
            current_pdf_path=row.get("current_pdf_path"),
            application_url=row.get("application_url"),
            filter_result=self._parse_json_field(row.get("filter_result")),
            error_message=row.get("error_message"),
            scrape_attempts=row.get("scrape_attempts") or 0,
            last_scrape_error=row.get("last_scrape_error"),
            last_scrape_attempt_at=self._normalize_datetime(row.get("last_scrape_attempt_at")),
            created_at=self._normalize_datetime(row.get("created_at")) or datetime.now(tz=timezone.utc),
            updated_at=self._normalize_datetime(row.get("updated_at")) or datetime.now(tz=timezone.utc),
        )

    def _cv_attempt_to_row(self, attempt: CVCompositionAttempt) -> dict:
        return {
            "job_id": attempt.job_id,
            "user_id": attempt.user_id,
            "attempt_number": attempt.attempt_number,
            "user_feedback": attempt.user_feedback,
            "cv_json": attempt.cv_json,
            "pdf_path": attempt.pdf_path,
            "created_at": attempt.created_at,
        }

    def _row_to_cv_attempt(self, row: dict) -> CVCompositionAttempt:
        return CVCompositionAttempt(
            job_id=row["job_id"],
            user_id=row.get("user_id", ""),
            attempt_number=row["attempt_number"],
            user_feedback=row.get("user_feedback"),
            cv_json=self._parse_json_field(row.get("cv_json")) or {},
            pdf_path=row.get("pdf_path"),
            created_at=self._normalize_datetime(row.get("created_at")) or datetime.now(tz=timezone.utc),
        )

    # =========================================================================
    # CRUD Methods
    # =========================================================================

    async def create(self, job: JobRecord) -> str:
        self._ensure_initialized()
        from .tables import Job

        existing = await Job.select().where(Job.job_id == job.job_id).first().run()
        if existing:
            raise RepositoryError(f"Job already exists: {job.job_id}", job.job_id)

        row_data = self._job_record_to_row(job)
        await Job.insert(Job(**row_data)).run()

        logger.debug(f"Created job {job.job_id}")
        return job.job_id

    async def get(self, job_id: str) -> JobRecord | None:
        self._ensure_initialized()
        from .tables import Job

        row = await Job.select().where(Job.job_id == job_id).first().run()
        if not row:
            return None
        return self._row_to_job_record(row)

    async def update(self, job_id: str, updates: dict) -> None:
        self._ensure_initialized()
        from .tables import Job

        existing = await Job.select().where(Job.job_id == job_id).first().run()
        if not existing:
            raise RepositoryError(f"Job not found: {job_id}", job_id)

        invalid_fields = set(updates.keys()) - UPDATABLE_FIELDS
        if invalid_fields:
            raise ValueError(f"Invalid update fields: {invalid_fields}")

        if "status" in updates:
            current_status = existing["status"]
            new_status = updates["status"]
            if not isinstance(current_status, BusinessState):
                current_status = BusinessState(current_status)
            if not isinstance(new_status, BusinessState):
                new_status = BusinessState(new_status)
            validate_transition(current_status, new_status, job_id)

        updates["updated_at"] = datetime.now(tz=timezone.utc)

        update_query = Job.update(updates).where(Job.job_id == job_id)
        await update_query.run()

        logger.debug(f"Updated job {job_id}: {list(updates.keys())}")

    async def delete(self, job_id: str) -> bool:
        self._ensure_initialized()
        from .tables import Job

        existing = await Job.select().where(Job.job_id == job_id).first().run()
        if not existing:
            return False

        await Job.delete().where(Job.job_id == job_id).run()
        logger.debug(f"Deleted job {job_id}")
        return True

    async def try_claim_failed_for_retry(self, job_id: str) -> JobRecord | None:
        """Atomic FAILED → QUEUED claim (SQLite, multi-worker safe)."""
        self._ensure_initialized()
        from .tables import Job

        async with Job._meta.db.transaction():
            existing = await Job.select().where(Job.job_id == job_id).first().run()
            if not existing:
                return None
            if existing.get("status") != BusinessState.FAILED.value:
                return None
            await (
                Job.update(
                    {
                        Job.status: BusinessState.QUEUED.value,
                        Job.error_message: None,
                        Job.last_scrape_error: None,
                        Job.updated_at: datetime.now(tz=timezone.utc),
                    }
                )
                .where(Job.job_id == job_id)
                .where(Job.status == BusinessState.FAILED.value)
                .run()
            )

        return await self.get(job_id)

    async def delete_for_user(self, job_id: str, user_id: str) -> bool:
        self._ensure_initialized()
        from .tables import Job

        existing = (
            await Job.select()
            .where(Job.job_id == job_id)
            .where(Job.user_id == user_id)
            .first()
            .run()
        )
        if not existing:
            return False
        return await self._cascade_delete_existing(job_id, existing)

    async def delete_cascade(self, job_id: str) -> bool:
        self._ensure_initialized()
        from .tables import Job

        existing = (
            await Job.select()
            .where(Job.job_id == job_id)
            .first()
            .run()
        )
        if not existing:
            return False
        return await self._cascade_delete_existing(job_id, existing)

    async def _cascade_delete_existing(self, job_id: str, existing: dict) -> bool:
        from .tables import CVAttemptTable, Job

        attempt_rows = (
            await CVAttemptTable.select(CVAttemptTable.pdf_path)
            .where(CVAttemptTable.job_id == job_id)
            .run()
        )

        pdf_paths: list[str] = []
        if existing.get("current_pdf_path"):
            pdf_paths.append(existing["current_pdf_path"])
        for row in attempt_rows:
            if row.get("pdf_path"):
                pdf_paths.append(row["pdf_path"])

        # Atomic so a mid-step failure can't orphan the job from its history.
        async with Job._meta.db.transaction():
            await CVAttemptTable.delete().where(CVAttemptTable.job_id == job_id).run()
            await Job.delete().where(Job.job_id == job_id).run()
        logger.info("Cascade-deleted job %s (%d pdfs)", job_id, len(pdf_paths))

        _unlink_pdfs(pdf_paths, job_id)
        return True

    # =========================================================================
    # Query Methods
    # =========================================================================

    async def get_for_user(self, job_id: str, user_id: str) -> JobRecord | None:
        self._ensure_initialized()
        from .tables import Job

        row = (
            await Job.select()
            .where(Job.job_id == job_id)
            .where(Job.user_id == user_id)
            .first()
            .run()
        )
        if not row:
            return None
        return self._row_to_job_record(row)

    async def get_pending(self, user_id: str) -> list[JobRecord]:
        self._ensure_initialized()
        from .tables import Job

        rows = (
            await Job.select()
            .where(Job.status == BusinessState.PENDING_REVIEW)
            .where(Job.user_id == user_id)
            .order_by(Job.created_at, ascending=False)
            .run()
        )

        return [self._row_to_job_record(row) for row in rows]

    async def get_by_status(
        self,
        user_id: str,
        status: str,
        limit: int = 100,
        offset: int = 0,
        order_by: str = "created_at",
        order_desc: bool = True,
    ) -> list[JobRecord]:
        self._ensure_initialized()
        from .tables import Job

        order_column = Job.updated_at if order_by == "updated_at" else Job.created_at

        rows = (
            await Job.select()
            .where(Job.status == status)
            .where(Job.user_id == user_id)
            .order_by(order_column, ascending=not order_desc)
            .limit(limit)
            .offset(offset)
            .run()
        )

        return [self._row_to_job_record(row) for row in rows]

    async def get_all(self, user_id: str, limit: int = 100, offset: int = 0) -> list[JobRecord]:
        self._ensure_initialized()
        from .tables import Job

        rows = (
            await Job.select()
            .where(Job.user_id == user_id)
            .order_by(Job.created_at, ascending=False)
            .limit(limit)
            .offset(offset)
            .run()
        )

        return [self._row_to_job_record(row) for row in rows]

    async def get_history(
        self,
        user_id: str,
        limit: int = 50,
        statuses: list[str] | None = None,
    ) -> list[JobRecord]:
        self._ensure_initialized()
        from .tables import Job

        query = (
            Job.select()
            .where(Job.user_id == user_id)
            .order_by(Job.updated_at, ascending=False)
            .limit(limit)
        )

        if statuses:
            query = query.where(Job.status.is_in(statuses))

        rows = await query.run()
        return [self._row_to_job_record(row) for row in rows]

    async def get_status_counts(self, user_id: str) -> dict[str, int]:
        self._ensure_initialized()

        conn = await self._engine.get_connection()
        try:
            cursor = await conn.execute(
                "SELECT status, COUNT(*) AS n FROM job WHERE user_id = ? GROUP BY status",
                (user_id,),
            )
            rows = await cursor.fetchall()
        finally:
            await conn.close()

        return {row["status"]: row["n"] for row in rows if row["status"]}

    # =========================================================================
    # CV Attempt Methods
    # =========================================================================

    async def create_cv_attempt(self, attempt: CVCompositionAttempt) -> None:
        self._ensure_initialized()
        from .tables import CVAttemptTable

        row_data = self._cv_attempt_to_row(attempt)
        await CVAttemptTable.insert(CVAttemptTable(**row_data)).run()
        logger.debug(
            f"Created CV attempt {attempt.attempt_number} for job {attempt.job_id}"
        )

    async def get_cv_attempts(self, job_id: str) -> list[CVCompositionAttempt]:
        self._ensure_initialized()
        from .tables import CVAttemptTable

        rows = (
            await CVAttemptTable.select()
            .where(CVAttemptTable.job_id == job_id)
            .order_by(CVAttemptTable.attempt_number, ascending=True)
            .run()
        )
        return [self._row_to_cv_attempt(row) for row in rows]

    async def get_latest_cv_attempt(self, job_id: str) -> CVCompositionAttempt | None:
        self._ensure_initialized()
        from .tables import CVAttemptTable

        row = (
            await CVAttemptTable.select()
            .where(CVAttemptTable.job_id == job_id)
            .order_by(CVAttemptTable.attempt_number, ascending=False)
            .first()
            .run()
        )
        if not row:
            return None
        return self._row_to_cv_attempt(row)

    # =========================================================================
    # Specialized Methods
    # =========================================================================

    async def find_by_application_url(self, url: str, user_id: str | None = None) -> JobRecord | None:
        self._ensure_initialized()
        from .tables import Job

        query = Job.select().where(Job.application_url == url)
        if user_id is not None:
            query = query.where(Job.user_id == user_id)
        row = await query.first().run()
        if not row:
            return None

        return self._row_to_job_record(row)

    async def cleanup(
        self,
        older_than_days: int,
        statuses: list[str],
        user_id: str | None = None,
    ) -> int:
        self._ensure_initialized()
        from .tables import CVAttemptTable, Job

        if older_than_days < 1:
            raise ValueError("older_than_days must be >= 1")
        if not statuses:
            raise ValueError("statuses list cannot be empty")

        cutoff_date = datetime.now(tz=timezone.utc) - timedelta(days=older_than_days)

        query = Job.select(Job.job_id).where(Job.status.is_in(statuses)).where(Job.created_at < cutoff_date)
        if user_id is not None:
            query = query.where(Job.user_id == user_id)

        to_delete = await query.run()
        count = len(to_delete)

        if count > 0:
            job_ids = [row["job_id"] for row in to_delete]
            async with Job._meta.db.transaction():
                await (
                    CVAttemptTable.delete()
                    .where(CVAttemptTable.job_id.is_in(job_ids))
                    .run()
                )
                await Job.delete().where(Job.job_id.is_in(job_ids)).run()

        logger.info(f"Cleanup: deleted {count} jobs older than {older_than_days} days")
        return count
