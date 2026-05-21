"""Tests for the promote_user CLI script.

Invokes the script's async entry point directly against a temp DB so we
don't pay the cost of spawning a subprocess on every test, and we can
reuse the pytest-asyncio event loop.
"""

import pytest
import pytest_asyncio

from scripts.promote_user import async_main, main
from src.models.user import UserRole
from src.services.auth.user_repository import UserRepository


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    """Spin up a fresh SQLite engine + UserRepository and seed two users."""
    from piccolo.engine.sqlite import SQLiteEngine

    from src.services.db.tables import MagicLinkTable, UserTable

    db_path = tmp_path / "promote.db"
    engine = SQLiteEngine(path=str(db_path))

    UserTable._meta._db = engine
    MagicLinkTable._meta._db = engine

    await UserTable.create_table(if_not_exists=True).run()
    await MagicLinkTable.create_table(if_not_exists=True).run()

    repo = UserRepository()
    await repo.initialize(db_path=str(db_path))
    await repo.create_user("alice@example.com")
    await repo.create_user("bob@example.com")

    yield str(db_path), repo
    await engine.close_connection_pool()


@pytest.mark.asyncio
async def test_promote_user_to_admin(temp_db, capsys):
    db_path, repo = temp_db

    rc = await async_main(
        ["--email", "alice@example.com", "--role", "admin", "--db-path", db_path]
    )
    assert rc == 0

    out = capsys.readouterr().out
    assert "Promoted alice@example.com to admin" in out

    fetched = await repo.get_by_email("alice@example.com")
    assert fetched.role == UserRole.ADMIN


@pytest.mark.asyncio
async def test_promote_defaults_to_admin(temp_db, capsys):
    db_path, repo = temp_db

    rc = await async_main(["--email", "bob@example.com", "--db-path", db_path])
    assert rc == 0
    assert "Promoted bob@example.com to admin" in capsys.readouterr().out

    fetched = await repo.get_by_email("bob@example.com")
    assert fetched.role == UserRole.ADMIN


@pytest.mark.asyncio
async def test_promote_to_premium(temp_db, capsys):
    db_path, repo = temp_db

    rc = await async_main(
        ["--email", "alice@example.com", "--role", "premium", "--db-path", db_path]
    )
    assert rc == 0
    assert "Promoted alice@example.com to premium" in capsys.readouterr().out

    fetched = await repo.get_by_email("alice@example.com")
    assert fetched.role == UserRole.PREMIUM


@pytest.mark.asyncio
async def test_promote_unknown_user_exits_nonzero(temp_db, capsys):
    db_path, _ = temp_db

    rc = await async_main(
        ["--email", "missing@example.com", "--role", "admin", "--db-path", db_path]
    )
    assert rc == 1

    err = capsys.readouterr().err
    assert "not found" in err


@pytest.mark.asyncio
async def test_missing_email_without_list_flag(temp_db, capsys):
    db_path, _ = temp_db

    rc = await async_main(["--db-path", db_path])
    assert rc == 1
    err = capsys.readouterr().err
    assert "--email is required" in err


@pytest.mark.asyncio
async def test_list_admins_empty(temp_db, capsys):
    db_path, _ = temp_db

    rc = await async_main(["--list-admins", "--db-path", db_path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "No admin users" in out


@pytest.mark.asyncio
async def test_list_admins_after_promotion(temp_db, capsys):
    db_path, repo = temp_db

    await repo.set_role(
        (await repo.get_by_email("alice@example.com")).id, UserRole.ADMIN
    )
    await repo.set_role(
        (await repo.get_by_email("bob@example.com")).id, UserRole.ADMIN
    )

    rc = await async_main(["--list-admins", "--db-path", db_path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Admins (2)" in out
    assert "alice@example.com" in out
    assert "bob@example.com" in out


def test_invalid_role_rejected_by_argparse(tmp_path, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "--email",
                "alice@example.com",
                "--role",
                "nope",
                "--db-path",
                str(tmp_path / "noop.db"),
            ]
        )

    assert excinfo.value.code != 0
