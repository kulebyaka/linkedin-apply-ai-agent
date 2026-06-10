"""Shared SQLite schema migrations.

Both `SQLiteJobRepository.initialize` and `UserRepository.initialize` call
`apply_migrations(engine)` on startup. A `schema_migrations` table records
which migrations have been applied so subsequent startups skip them.

Each migration's internal predicate inspects the actual schema and turns into
a no-op when the change is already present. That handles back-fill: on the
first run against a legacy DB whose columns were already added by an older
version of this code, each migration self-detects "already applied", writes
its name into `schema_migrations`, and never tries the ALTER again.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


@dataclass(frozen=True)
class Migration:
    """A single schema migration.

    `migrate(conn)` should inspect the current schema and:
    - return True  → SQL was executed
    - return False → no-op (already applied / never needed); record as applied
    - return None  → not applicable yet (e.g., target table doesn't exist);
                     do NOT record, so it will be retried on the next call.
    """

    name: str
    migrate: Callable[[object], Awaitable[bool | None]]
    post_apply: Callable[[object], Awaitable[None]] | None = None


async def _table_exists(conn, table: str) -> bool:
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return await cursor.fetchone() is not None


async def _columns_for(conn, table: str) -> set[str]:
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return {row["name"] for row in rows}


def _rename_column(table: str, old: str, new: str) -> Callable[[object], Awaitable[bool | None]]:
    async def runner(conn) -> bool | None:
        if not await _table_exists(conn, table):
            return None  # table not created yet — try again next run
        cols = await _columns_for(conn, table)
        if old in cols and new not in cols:
            logger.info("Migrating: rename %s.%s -> %s.%s", table, old, table, new)
            await conn.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
            return True
        return False

    return runner


def _add_column(
    table: str, column: str, ddl_suffix: str
) -> Callable[[object], Awaitable[bool | None]]:
    async def runner(conn) -> bool | None:
        if not await _table_exists(conn, table):
            return None  # table not created yet — try again next run
        cols = await _columns_for(conn, table)
        if column not in cols:
            logger.info("Migrating: add column %s.%s", table, column)
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl_suffix}")
            return True
        return False

    return runner


async def _reindex_user(conn) -> None:
    # SQLite lets CREATE INDEX reference a not-yet-existing column,
    # so pre-existing rows are missing from the index until REINDEX.
    logger.info("Rebuilding indexes on user table after migration")
    await conn.execute('REINDEX "user"')


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        "rename_job_cv_json_to_current_cv_json",
        _rename_column("job", "cv_json", "current_cv_json"),
    ),
    Migration(
        "rename_job_pdf_path_to_current_pdf_path",
        _rename_column("job", "pdf_path", "current_pdf_path"),
    ),
    Migration(
        "add_job_user_id",
        _add_column("job", "user_id", "user_id VARCHAR(36) NOT NULL DEFAULT ''"),
    ),
    Migration(
        "add_job_filter_result",
        _add_column("job", "filter_result", "filter_result JSON NULL"),
    ),
    Migration(
        "add_job_scrape_attempts",
        _add_column("job", "scrape_attempts", "scrape_attempts INTEGER NOT NULL DEFAULT 0"),
    ),
    Migration(
        "add_job_last_scrape_error",
        _add_column("job", "last_scrape_error", "last_scrape_error TEXT NULL"),
    ),
    Migration(
        "add_job_last_scrape_attempt_at",
        _add_column("job", "last_scrape_attempt_at", "last_scrape_attempt_at TIMESTAMP NULL"),
    ),
    Migration(
        "add_cv_attempt_user_id",
        _add_column("cv_attempt", "user_id", "user_id VARCHAR(36) NOT NULL DEFAULT ''"),
    ),
    Migration(
        "add_user_filter_preferences",
        _add_column("user", "filter_preferences", "filter_preferences JSON NULL"),
    ),
    Migration(
        "add_user_model_preferences",
        _add_column("user", "model_preferences", "model_preferences JSON NULL"),
    ),
    Migration(
        "add_user_role",
        _add_column("user", "role", "role VARCHAR(20) NOT NULL DEFAULT 'trial'"),
        post_apply=_reindex_user,
    ),
    Migration(
        "add_job_recovery_attempts",
        _add_column("job", "recovery_attempts", "recovery_attempts INTEGER NOT NULL DEFAULT 0"),
    ),
    Migration(
        "add_job_last_recovery_attempt_at",
        _add_column("job", "last_recovery_attempt_at", "last_recovery_attempt_at TIMESTAMP NULL"),
    ),
    Migration(
        "add_job_workflow_step",
        _add_column("job", "workflow_step", "workflow_step VARCHAR(40) NULL"),
    ),
    Migration(
        "add_job_session_authenticated",
        _add_column("job", "session_authenticated", "session_authenticated BOOLEAN NULL"),
    ),
    Migration(
        "add_job_decline_reason",
        _add_column("job", "decline_reason", "decline_reason TEXT NULL"),
    ),
    Migration(
        "add_job_override_reason",
        _add_column("job", "override_reason", "override_reason TEXT NULL"),
    ),
    Migration(
        "add_job_refine_signal_state",
        _add_column("job", "refine_signal_state", "refine_signal_state VARCHAR(20) NULL"),
    ),
    Migration(
        "add_user_pending_refinement",
        _add_column("user", "pending_refinement", "pending_refinement JSON NULL"),
    ),
)


async def apply_migrations(
    engine,
    migrations: tuple[Migration, ...] = MIGRATIONS,
) -> None:
    """Apply pending schema migrations using a SQLite engine.

    Safe to call multiple times and from multiple entry points (job and user
    repositories both invoke it on init).
    """
    conn = await engine.get_connection()
    try:
        await conn.execute(_SCHEMA_MIGRATIONS_DDL)
        cursor = await conn.execute("SELECT name FROM schema_migrations")
        rows = await cursor.fetchall()
        applied: set[str] = {row["name"] for row in rows}

        for migration in migrations:
            if migration.name in applied:
                continue
            result = await migration.migrate(conn)
            if result is None:
                # Target table absent — leave unrecorded so a later caller
                # (e.g., SQLiteJobRepository.initialize after this one) can
                # actually apply it.
                continue
            if result and migration.post_apply is not None:
                await migration.post_apply(conn)
            await conn.execute(
                "INSERT INTO schema_migrations (name) VALUES (?)",
                (migration.name,),
            )

        await conn.commit()
    finally:
        await conn.close()
