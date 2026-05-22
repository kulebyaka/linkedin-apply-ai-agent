"""Tests for the `role` column on the user table and related repo methods.

Covers:
- Fresh DB: newly created user has role == "trial".
- Old DB without `role` column: UserRepository.initialize() migrates it,
  existing rows default to "trial".
- set_role: persists and round-trips through get_by_id / get_by_email.
- list_all_users: returns users ordered by created_at desc.
"""

import sqlite3

import pytest
import pytest_asyncio

from src.models.user import UserRole
from src.services.auth.user_repository import UserRepository


@pytest_asyncio.fixture
async def repo(tmp_path):
    """Set up a UserRepository against a fresh SQLite database."""
    from piccolo.engine.sqlite import SQLiteEngine

    from src.services.db.tables import MagicLinkTable, UserTable

    db_path = tmp_path / "users.db"
    engine = SQLiteEngine(path=str(db_path))

    UserTable._meta._db = engine
    MagicLinkTable._meta._db = engine

    await UserTable.create_table(if_not_exists=True).run()
    await MagicLinkTable.create_table(if_not_exists=True).run()

    repo = UserRepository()
    # Run initialize() so the migration block executes — engine is already set,
    # so the inner create_table guard is skipped, but PRAGMA migration still runs.
    await repo.initialize(db_path=str(db_path))
    yield repo
    await engine.close_connection_pool()


@pytest.mark.asyncio
async def test_new_user_defaults_to_trial_role(repo):
    user = await repo.create_user("trial@example.com", "Trial User")
    assert user.role == UserRole.TRIAL

    fetched = await repo.get_by_id(user.id)
    assert fetched is not None
    assert fetched.role == UserRole.TRIAL


@pytest.mark.asyncio
async def test_set_role_persists_and_round_trips(repo):
    user = await repo.create_user("promote@example.com")

    updated = await repo.set_role(user.id, UserRole.ADMIN)
    assert updated.role == UserRole.ADMIN

    fetched = await repo.get_by_id(user.id)
    assert fetched.role == UserRole.ADMIN

    # demote to premium
    demoted = await repo.set_role(user.id, UserRole.PREMIUM)
    assert demoted.role == UserRole.PREMIUM
    by_email = await repo.get_by_email("promote@example.com")
    assert by_email.role == UserRole.PREMIUM


@pytest.mark.asyncio
async def test_set_role_unknown_user_raises(repo):
    with pytest.raises(KeyError):
        await repo.set_role("nonexistent-id", UserRole.ADMIN)


@pytest.mark.asyncio
async def test_list_all_users_orders_by_created_at_desc(repo):
    import asyncio

    u1 = await repo.create_user("first@example.com")
    await asyncio.sleep(0.01)
    u2 = await repo.create_user("second@example.com")
    await asyncio.sleep(0.01)
    u3 = await repo.create_user("third@example.com")

    users = await repo.list_all_users()
    assert [u.id for u in users] == [u3.id, u2.id, u1.id]


@pytest.mark.asyncio
async def test_list_all_users_pagination(repo):
    for i in range(5):
        await repo.create_user(f"user{i}@example.com")

    page1 = await repo.list_all_users(limit=2, offset=0)
    page2 = await repo.list_all_users(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert {u.id for u in page1}.isdisjoint({u.id for u in page2})


@pytest.mark.asyncio
async def test_count_admins_counts_only_admin_role(repo):
    await repo.create_user("trial@example.com")
    a1 = await repo.create_user("admin1@example.com")
    a2 = await repo.create_user("admin2@example.com")
    await repo.set_role(a1.id, UserRole.ADMIN)
    await repo.set_role(a2.id, UserRole.ADMIN)

    assert await repo.count_admins() == 2

    await repo.set_role(a2.id, UserRole.TRIAL)
    assert await repo.count_admins() == 1


@pytest.mark.asyncio
async def test_migration_adds_role_column_to_old_db(tmp_path):
    """Simulate an old database missing the `role` column and verify migration."""
    from piccolo.engine.sqlite import SQLiteEngine

    from src.services.db.tables import MagicLinkTable, UserTable

    db_path = tmp_path / "legacy.db"

    # 1. Hand-build the legacy schema WITHOUT the `role` column.
    raw = sqlite3.connect(db_path)
    raw.execute(
        """
        CREATE TABLE "user" (
            "id" VARCHAR(36) PRIMARY KEY NOT NULL,
            "email" VARCHAR(255) NOT NULL UNIQUE,
            "display_name" VARCHAR(100) NOT NULL DEFAULT '',
            "master_cv_json" JSON,
            "search_preferences" JSON,
            "created_at" TIMESTAMP NOT NULL,
            "updated_at" TIMESTAMP NOT NULL
        )
        """
    )
    raw.execute(
        """
        CREATE TABLE "magic_link" (
            "token" VARCHAR(64) PRIMARY KEY NOT NULL,
            "email" VARCHAR(255) NOT NULL,
            "expires_at" TIMESTAMP NOT NULL,
            "used" INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    # Insert a pre-existing user row.
    raw.execute(
        "INSERT INTO \"user\" (id, email, display_name, master_cv_json, "
        "search_preferences, created_at, updated_at) VALUES "
        "('legacy-id', 'legacy@example.com', 'Legacy User', NULL, NULL, "
        "'2024-01-01 00:00:00', '2024-01-01 00:00:00')"
    )
    raw.commit()

    # Verify role column is absent before migration.
    cols_before = {r[1] for r in raw.execute("PRAGMA table_info(user)").fetchall()}
    assert "role" not in cols_before
    raw.close()

    # 2. Configure Piccolo engine on the same DB file and run initialize().
    engine = SQLiteEngine(path=str(db_path))
    UserTable._meta._db = engine
    MagicLinkTable._meta._db = engine

    repo = UserRepository()
    await repo.initialize(db_path=str(db_path))

    # 3. The column should now exist and the legacy row should be readable as trial.
    raw = sqlite3.connect(db_path)
    cols_after = {r[1] for r in raw.execute("PRAGMA table_info(user)").fetchall()}
    assert "role" in cols_after
    row = raw.execute('SELECT role FROM "user" WHERE id=?', ("legacy-id",)).fetchone()
    assert row[0] == "trial"
    raw.close()

    legacy = await repo.get_by_id("legacy-id")
    assert legacy is not None
    assert legacy.role == UserRole.TRIAL


@pytest.mark.asyncio
async def test_migration_leaves_role_index_consistent(tmp_path):
    """Pre-existing rows must end up in user_role; migration must REINDEX."""
    from piccolo.engine.sqlite import SQLiteEngine

    from src.services.db.tables import MagicLinkTable, UserTable

    db_path = tmp_path / "legacy_indexed.db"

    raw = sqlite3.connect(db_path)
    raw.execute(
        """
        CREATE TABLE "user" (
            "id" VARCHAR(36) PRIMARY KEY NOT NULL,
            "email" VARCHAR(255) NOT NULL UNIQUE,
            "display_name" VARCHAR(100) NOT NULL DEFAULT '',
            "master_cv_json" JSON,
            "search_preferences" JSON,
            "created_at" TIMESTAMP NOT NULL,
            "updated_at" TIMESTAMP NOT NULL
        )
        """
    )
    raw.execute(
        """
        CREATE TABLE "magic_link" (
            "token" VARCHAR(64) PRIMARY KEY NOT NULL,
            "email" VARCHAR(255) NOT NULL,
            "expires_at" TIMESTAMP NOT NULL,
            "used" INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    # Two pre-existing rows that must end up in the role index.
    for i in range(2):
        raw.execute(
            'INSERT INTO "user" (id, email, display_name, master_cv_json, '
            "search_preferences, created_at, updated_at) VALUES "
            f"('legacy-{i}', 'legacy{i}@example.com', 'Legacy {i}', NULL, NULL, "
            "'2024-01-01 00:00:00', '2024-01-01 00:00:00')"
        )
    raw.commit()
    raw.close()

    engine = SQLiteEngine(path=str(db_path))
    UserTable._meta._db = engine
    MagicLinkTable._meta._db = engine

    # Reproduce the production order: create_table runs CREATE INDEX
    # against the not-yet-existing role column before ALTER TABLE adds it.
    await UserTable.create_table(if_not_exists=True).run()
    await MagicLinkTable.create_table(if_not_exists=True).run()

    repo = UserRepository()
    await repo.initialize(db_path=str(db_path))

    # Database must pass integrity_check — no "row N missing from index".
    raw = sqlite3.connect(db_path)
    integrity = [r[0] for r in raw.execute("PRAGMA integrity_check").fetchall()]
    raw.close()
    assert integrity == ["ok"], (
        f"DB integrity broken after migration: {integrity}"
    )

    await engine.close_connection_pool()
