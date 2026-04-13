"""Unit tests for UserRepository.

Tests CRUD operations, magic link management, and search preferences.
Uses temporary SQLite database for isolation.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from src.models.user import UserSearchPreferences
from src.services.user_repository import UserRepository


@pytest_asyncio.fixture
async def db_and_repo(tmp_path):
    """Set up a temporary SQLite database with user tables and return a UserRepository."""
    from piccolo.engine.sqlite import SQLiteEngine

    from src.services.tables import MagicLinkTable, UserTable

    db_path = tmp_path / "test_users.db"
    engine = SQLiteEngine(path=str(db_path))

    UserTable._meta._db = engine
    MagicLinkTable._meta._db = engine

    await UserTable.create_table(if_not_exists=True).run()
    await MagicLinkTable.create_table(if_not_exists=True).run()

    repo = UserRepository()
    yield repo

    await engine.close_connection_pool()


# =============================================================================
# User CRUD Tests
# =============================================================================


@pytest.mark.asyncio
async def test_create_user(db_and_repo):
    repo = db_and_repo
    user = await repo.create_user("alice@example.com", "Alice")

    assert user.email == "alice@example.com"
    assert user.display_name == "Alice"
    assert user.id  # UUID assigned
    assert user.master_cv_json is None
    assert user.search_preferences is None


@pytest.mark.asyncio
async def test_create_user_default_display_name(db_and_repo):
    repo = db_and_repo
    user = await repo.create_user("bob@example.com")

    assert user.display_name == "bob"


@pytest.mark.asyncio
async def test_get_by_id(db_and_repo):
    repo = db_and_repo
    created = await repo.create_user("test@example.com", "Test")

    found = await repo.get_by_id(created.id)
    assert found is not None
    assert found.email == "test@example.com"
    assert found.display_name == "Test"


@pytest.mark.asyncio
async def test_get_by_id_not_found(db_and_repo):
    repo = db_and_repo
    found = await repo.get_by_id("nonexistent-id")
    assert found is None


@pytest.mark.asyncio
async def test_get_by_email(db_and_repo):
    repo = db_and_repo
    await repo.create_user("find@example.com", "FindMe")

    found = await repo.get_by_email("find@example.com")
    assert found is not None
    assert found.display_name == "FindMe"


@pytest.mark.asyncio
async def test_get_by_email_not_found(db_and_repo):
    repo = db_and_repo
    found = await repo.get_by_email("noone@nowhere.com")
    assert found is None


@pytest.mark.asyncio
async def test_update_display_name(db_and_repo):
    repo = db_and_repo
    user = await repo.create_user("update@example.com", "Old Name")

    updated = await repo.update(user.id, {"display_name": "New Name"})
    assert updated.display_name == "New Name"
    assert updated.email == "update@example.com"


@pytest.mark.asyncio
async def test_update_master_cv(db_and_repo):
    repo = db_and_repo
    user = await repo.create_user("cv@example.com")

    cv = {"contact": {"full_name": "CV User"}, "skills": ["python", "rust"]}
    updated = await repo.update(user.id, {"master_cv_json": cv})
    assert updated.master_cv_json == cv


@pytest.mark.asyncio
async def test_update_search_preferences(db_and_repo):
    repo = db_and_repo
    user = await repo.create_user("prefs@example.com")

    prefs = UserSearchPreferences(keywords="python", location="Berlin", easy_apply_only=True)
    updated = await repo.update(user.id, {"search_preferences": prefs})

    assert updated.search_preferences is not None
    assert updated.search_preferences.keywords == "python"
    assert updated.search_preferences.location == "Berlin"
    assert updated.search_preferences.easy_apply_only is True


@pytest.mark.asyncio
async def test_update_nonexistent_user_raises(db_and_repo):
    repo = db_and_repo
    with pytest.raises(KeyError):
        await repo.update("nonexistent", {"display_name": "Oops"})


# =============================================================================
# Search Preferences Query Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_all_with_search_prefs_empty(db_and_repo):
    repo = db_and_repo
    # User without search prefs
    await repo.create_user("nopref@example.com")

    result = await repo.get_all_with_search_prefs()
    assert result == []


@pytest.mark.asyncio
async def test_get_all_with_search_prefs_returns_configured(db_and_repo):
    repo = db_and_repo

    # User without prefs
    await repo.create_user("no@example.com")

    # User with prefs
    user_with = await repo.create_user("yes@example.com")
    prefs = UserSearchPreferences(keywords="data engineer")
    await repo.update(user_with.id, {"search_preferences": prefs})

    result = await repo.get_all_with_search_prefs()
    assert len(result) == 1
    assert result[0].email == "yes@example.com"
    assert result[0].search_preferences.keywords == "data engineer"


@pytest.mark.asyncio
async def test_get_all_with_search_prefs_multiple_users(db_and_repo):
    repo = db_and_repo

    user1 = await repo.create_user("user1@example.com")
    user2 = await repo.create_user("user2@example.com")
    await repo.create_user("user3@example.com")  # no prefs

    await repo.update(user1.id, {"search_preferences": UserSearchPreferences(keywords="python")})
    await repo.update(user2.id, {"search_preferences": UserSearchPreferences(keywords="java")})

    result = await repo.get_all_with_search_prefs()
    assert len(result) == 2
    emails = {u.email for u in result}
    assert emails == {"user1@example.com", "user2@example.com"}


# =============================================================================
# Magic Link Tests
# =============================================================================


@pytest.mark.asyncio
async def test_create_and_verify_magic_link(db_and_repo):
    repo = db_and_repo
    token = "test-token-123"
    expires = datetime.now(tz=timezone.utc) + timedelta(minutes=15)

    await repo.create_magic_link("magic@example.com", token, expires)

    email = await repo.verify_magic_link(token)
    assert email == "magic@example.com"


@pytest.mark.asyncio
async def test_verify_magic_link_marks_as_used(db_and_repo):
    repo = db_and_repo
    token = "use-once-token"
    expires = datetime.now(tz=timezone.utc) + timedelta(minutes=15)

    await repo.create_magic_link("once@example.com", token, expires)

    # First verification succeeds
    email = await repo.verify_magic_link(token)
    assert email == "once@example.com"

    # Second verification fails (already used)
    email2 = await repo.verify_magic_link(token)
    assert email2 is None


@pytest.mark.asyncio
async def test_verify_expired_magic_link(db_and_repo):
    repo = db_and_repo
    token = "expired-token"
    expires = datetime.now(tz=timezone.utc) - timedelta(minutes=1)

    await repo.create_magic_link("expired@example.com", token, expires)

    email = await repo.verify_magic_link(token)
    assert email is None


@pytest.mark.asyncio
async def test_verify_nonexistent_token(db_and_repo):
    repo = db_and_repo
    email = await repo.verify_magic_link("does-not-exist")
    assert email is None


@pytest.mark.asyncio
async def test_cleanup_expired_magic_links(db_and_repo):
    repo = db_and_repo

    # Create one expired and one valid token
    expired_time = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    valid_time = datetime.now(tz=timezone.utc) + timedelta(hours=1)

    await repo.create_magic_link("old@example.com", "old-token", expired_time)
    await repo.create_magic_link("new@example.com", "new-token", valid_time)

    deleted = await repo.cleanup_expired_magic_links()
    assert deleted == 1

    # Valid token should still work
    email = await repo.verify_magic_link("new-token")
    assert email == "new@example.com"

    # Expired token is gone
    email_old = await repo.verify_magic_link("old-token")
    assert email_old is None
