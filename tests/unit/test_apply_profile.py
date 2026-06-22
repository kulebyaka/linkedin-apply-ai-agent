"""Tests for ApplyProfile + auto_apply: model, state machine, and persistence.

Covers:
- ApplyProfile round-trip (model_dump / re-parse) and is_complete_for helper.
- New BusinessState transitions (APPROVED->NEEDS_EXTENSION, NEEDS_EXTENSION->APPLYING,
  APPLYING->MANUAL_REQUIRED, QUEUED/PROCESSING->APPROVED) and illegal ones.
- UserRepository save/load of apply_profile + auto_apply.
- Runtime migration of both columns on a column-less (legacy) DB.
"""

import sqlite3

import pytest
import pytest_asyncio

from src.models.state_machine import (
    BusinessState,
    InvalidStateTransitionError,
    validate_transition,
)
from src.models.user import ApplyProfile
from src.services.auth.user_repository import UserRepository

# =============================================================================
# ApplyProfile model
# =============================================================================


def test_apply_profile_round_trip():
    profile = ApplyProfile(
        phone_country_code="+1",
        years_experience=7,
        expected_salary="120000",
        needs_visa_sponsorship=False,
        legally_authorized=True,
        willing_to_relocate=False,
        drivers_license=True,
    )
    dumped = profile.model_dump()
    restored = ApplyProfile(**dumped)
    assert restored == profile


def test_apply_profile_all_optional():
    """Every field defaults to None ("unknown")."""
    profile = ApplyProfile()
    assert profile.phone_country_code is None
    assert profile.years_experience is None
    assert profile.expected_salary is None
    assert profile.needs_visa_sponsorship is None
    assert profile.legally_authorized is None
    assert profile.willing_to_relocate is None
    assert profile.drivers_license is None


def test_is_complete_for_all_present():
    profile = ApplyProfile(years_experience=5, legally_authorized=True)
    assert profile.is_complete_for({"years_experience", "legally_authorized"}) is True


def test_is_complete_for_missing_value():
    profile = ApplyProfile(years_experience=5)
    # legally_authorized is None -> incomplete
    assert profile.is_complete_for({"years_experience", "legally_authorized"}) is False


def test_is_complete_for_empty_set_is_complete():
    assert ApplyProfile().is_complete_for(set()) is True


def test_is_complete_for_unknown_kind_is_incomplete():
    """A kind with no matching attribute can never be satisfied."""
    assert ApplyProfile().is_complete_for({"nonexistent_kind"}) is False


def test_is_complete_for_false_bool_counts_as_known():
    """A boolean answer of False is a known value, not 'unknown'."""
    profile = ApplyProfile(needs_visa_sponsorship=False, willing_to_relocate=False)
    assert (
        profile.is_complete_for({"needs_visa_sponsorship", "willing_to_relocate"})
        is True
    )


# =============================================================================
# State machine transitions
# =============================================================================


@pytest.mark.parametrize(
    "current,target",
    [
        (BusinessState.APPROVED, BusinessState.NEEDS_EXTENSION),
        (BusinessState.APPROVED, BusinessState.MANUAL_REQUIRED),
        (BusinessState.APPLYING, BusinessState.MANUAL_REQUIRED),
        (BusinessState.NEEDS_EXTENSION, BusinessState.APPLYING),
        (BusinessState.NEEDS_EXTENSION, BusinessState.FAILED),
        (BusinessState.QUEUED, BusinessState.APPROVED),
        (BusinessState.PROCESSING, BusinessState.APPROVED),
    ],
)
def test_new_valid_transitions(current, target):
    assert validate_transition(current, target) is True


@pytest.mark.parametrize(
    "current,target",
    [
        # MANUAL_REQUIRED is terminal.
        (BusinessState.MANUAL_REQUIRED, BusinessState.APPLYING),
        (BusinessState.MANUAL_REQUIRED, BusinessState.APPLIED),
        (BusinessState.MANUAL_REQUIRED, BusinessState.FAILED),
        # NEEDS_EXTENSION cannot jump straight to applied.
        (BusinessState.NEEDS_EXTENSION, BusinessState.APPLIED),
    ],
)
def test_new_invalid_transitions_raise(current, target):
    with pytest.raises(InvalidStateTransitionError):
        validate_transition(current, target)


def test_manual_required_is_terminal():
    assert BusinessState.MANUAL_REQUIRED.is_terminal() is True


def test_needs_extension_is_not_terminal():
    assert BusinessState.NEEDS_EXTENSION.is_terminal() is False


# =============================================================================
# Repository persistence
# =============================================================================


@pytest_asyncio.fixture
async def repo(tmp_path):
    from piccolo.engine.sqlite import SQLiteEngine

    from src.services.db.tables import MagicLinkTable, NotificationTable, UserTable

    db_path = tmp_path / "apply.db"
    engine = SQLiteEngine(path=str(db_path))

    UserTable._meta._db = engine
    MagicLinkTable._meta._db = engine
    NotificationTable._meta._db = engine

    await UserTable.create_table(if_not_exists=True).run()
    await MagicLinkTable.create_table(if_not_exists=True).run()
    await NotificationTable.create_table(if_not_exists=True).run()

    r = UserRepository()
    await r.initialize(db_path=str(db_path))
    yield r
    await engine.close_connection_pool()


@pytest.mark.asyncio
async def test_save_and_load_apply_profile_and_auto_apply(repo):
    user = await repo.create_user("apply@example.com")
    assert user.apply_profile is None
    assert user.auto_apply is False

    profile = ApplyProfile(years_experience=4, legally_authorized=True)
    updated = await repo.update(
        user.id, {"apply_profile": profile, "auto_apply": True}
    )
    assert updated.apply_profile == profile
    assert updated.auto_apply is True

    fetched = await repo.get_by_id(user.id)
    assert fetched.apply_profile == profile
    assert fetched.auto_apply is True


@pytest.mark.asyncio
async def test_update_apply_profile_as_dict(repo):
    user = await repo.create_user("dict@example.com")
    updated = await repo.update(
        user.id, {"apply_profile": {"expected_salary": "90000"}}
    )
    assert updated.apply_profile == ApplyProfile(expected_salary="90000")


@pytest.mark.asyncio
async def test_auto_apply_defaults_false_when_not_updated(repo):
    user = await repo.create_user("default@example.com")
    updated = await repo.update(user.id, {"display_name": "Renamed"})
    assert updated.auto_apply is False
    assert updated.apply_profile is None


@pytest.mark.asyncio
async def test_migration_adds_apply_columns_to_old_db(tmp_path):
    """Legacy DB missing apply_profile/auto_apply gets migrated on initialize()."""
    from piccolo.engine.sqlite import SQLiteEngine

    from src.services.db.tables import MagicLinkTable, NotificationTable, UserTable

    db_path = tmp_path / "legacy_apply.db"

    raw = sqlite3.connect(db_path)
    raw.execute(
        """
        CREATE TABLE "user" (
            "id" VARCHAR(36) PRIMARY KEY NOT NULL,
            "email" VARCHAR(255) NOT NULL UNIQUE,
            "display_name" VARCHAR(100) NOT NULL DEFAULT '',
            "role" VARCHAR(20) NOT NULL DEFAULT 'trial',
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
    raw.execute(
        "INSERT INTO \"user\" (id, email, display_name, role, master_cv_json, "
        "search_preferences, created_at, updated_at) VALUES "
        "('legacy-id', 'legacy@example.com', 'Legacy', 'trial', NULL, NULL, "
        "'2024-01-01 00:00:00', '2024-01-01 00:00:00')"
    )
    raw.commit()

    cols_before = {r[1] for r in raw.execute("PRAGMA table_info(user)").fetchall()}
    assert "apply_profile" not in cols_before
    assert "auto_apply" not in cols_before
    raw.close()

    engine = SQLiteEngine(path=str(db_path))
    UserTable._meta._db = engine
    MagicLinkTable._meta._db = engine
    NotificationTable._meta._db = engine

    r = UserRepository()
    await r.initialize(db_path=str(db_path))

    raw = sqlite3.connect(db_path)
    cols_after = {r2[1] for r2 in raw.execute("PRAGMA table_info(user)").fetchall()}
    assert "apply_profile" in cols_after
    assert "auto_apply" in cols_after
    raw.close()

    legacy = await r.get_by_id("legacy-id")
    assert legacy is not None
    assert legacy.apply_profile is None
    assert legacy.auto_apply is False

    await engine.close_connection_pool()
