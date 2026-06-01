"""Admin-scope raw-SQL queries for SQLiteJobRepository.

Mixin class used by `SQLiteJobRepository`. Pulled out so the main repository
file is not dominated by the cross-user reporting paths.

Each method expects `self._engine` (Piccolo SQLiteEngine) and `self._ensure_initialized`
to be provided by the concrete repository.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.models.unified import JobRecord


class SQLiteAdminQueriesMixin:
    """Cross-user reporting queries for `SQLiteJobRepository`.

    Implemented as raw SQL — Piccolo's query builder doesn't compose well with
    the conditional WHERE list these endpoints need.
    """

    # These attributes/methods are supplied by SQLiteJobRepository.
    _engine: object
    def _ensure_initialized(self) -> None: ...  # pragma: no cover - typing only
    def _row_to_job_record(self, row: dict) -> JobRecord: ...  # pragma: no cover
    def _normalize_datetime(self, dt): ...  # pragma: no cover - typing only

    def _build_admin_filter_sql(
        self,
        *,
        user_ids: list[str] | None,
        statuses: list[str] | None,
        sources: list[str] | None,
        created_from: datetime | None,
        created_to: datetime | None,
        search: str | None,
    ) -> tuple[str, list]:
        """Build the WHERE clause + params list for admin list/count queries."""
        where_parts: list[str] = []
        params: list = []
        if user_ids:
            ph = ",".join(["?"] * len(user_ids))
            where_parts.append(f"user_id IN ({ph})")
            params.extend(user_ids)
        if statuses:
            ph = ",".join(["?"] * len(statuses))
            where_parts.append(f"status IN ({ph})")
            params.extend(statuses)
        if sources:
            ph = ",".join(["?"] * len(sources))
            where_parts.append(f"source IN ({ph})")
            params.extend(sources)
        if created_from is not None:
            where_parts.append("created_at >= ?")
            params.append(created_from)
        if created_to is not None:
            where_parts.append("created_at <= ?")
            params.append(created_to)
        if search:
            pat = f"%{search}%"
            where_parts.append(
                "("
                "COALESCE(json_extract(job_posting, '$.title'), '') LIKE ? "
                "OR COALESCE(json_extract(job_posting, '$.company'), '') LIKE ? "
                "OR COALESCE(json_extract(job_posting, '$.description'), '') LIKE ? "
                "OR COALESCE(error_message, '') LIKE ?"
                ")"
            )
            params.extend([pat, pat, pat, pat])
        where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
        return where_sql, params

    async def list_all_jobs(
        self,
        *,
        user_ids: list[str] | None = None,
        statuses: list[str] | None = None,
        sources: list[str] | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[JobRecord]:
        self._ensure_initialized()

        where_sql, params = self._build_admin_filter_sql(
            user_ids=user_ids,
            statuses=statuses,
            sources=sources,
            created_from=created_from,
            created_to=created_to,
            search=search,
        )
        sql = (
            f"SELECT * FROM job{where_sql} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        conn = await self._engine.get_connection()
        try:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
        finally:
            await conn.close()
        return [self._row_to_job_record(dict(row)) for row in rows]

    async def count_all_jobs(
        self,
        *,
        user_ids: list[str] | None = None,
        statuses: list[str] | None = None,
        sources: list[str] | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        search: str | None = None,
    ) -> int:
        self._ensure_initialized()

        where_sql, params = self._build_admin_filter_sql(
            user_ids=user_ids,
            statuses=statuses,
            sources=sources,
            created_from=created_from,
            created_to=created_to,
            search=search,
        )
        sql = f"SELECT COUNT(*) AS n FROM job{where_sql}"

        conn = await self._engine.get_connection()
        try:
            cursor = await conn.execute(sql, params)
            row = await cursor.fetchone()
        finally:
            await conn.close()
        return int(row["n"]) if row else 0

    async def count_by_status_global(
        self, window_hours: int | None = None
    ) -> dict[str, int]:
        self._ensure_initialized()

        if window_hours is not None:
            cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=window_hours)
            sql = (
                "SELECT status, COUNT(*) AS n FROM job WHERE created_at >= ? "
                "GROUP BY status"
            )
            params: tuple = (cutoff,)
        else:
            sql = "SELECT status, COUNT(*) AS n FROM job GROUP BY status"
            params = ()

        conn = await self._engine.get_connection()
        try:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
        finally:
            await conn.close()
        return {row["status"]: row["n"] for row in rows if row["status"]}

    async def get_latest_session_auth(self) -> dict | None:
        self._ensure_initialized()
        sql = (
            "SELECT job_id, session_authenticated, created_at FROM job "
            "WHERE source = 'linkedin' AND session_authenticated IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1"
        )
        conn = await self._engine.get_connection()
        try:
            cursor = await conn.execute(sql)
            row = await cursor.fetchone()
        finally:
            await conn.close()
        if row is None:
            return None
        scraped_at = self._normalize_datetime(row["created_at"])
        return {
            "authenticated": bool(row["session_authenticated"]),
            "job_id": row["job_id"],
            "scraped_at": scraped_at.isoformat() if scraped_at else None,
        }

    async def list_jobs_with_errors(
        self,
        limit: int = 50,
        offset: int = 0,
        since: datetime | None = None,
    ) -> list[JobRecord]:
        self._ensure_initialized()

        where_clauses = ["(error_message IS NOT NULL OR last_scrape_error IS NOT NULL)"]
        params: list = []
        if since is not None:
            where_clauses.append("updated_at >= ?")
            params.append(
                since if since.tzinfo else since.replace(tzinfo=timezone.utc)
            )
        sql = (
            "SELECT * FROM job WHERE "
            + " AND ".join(where_clauses)
            + " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        conn = await self._engine.get_connection()
        try:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
        finally:
            await conn.close()
        return [self._row_to_job_record(dict(row)) for row in rows]
