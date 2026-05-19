"""Unit tests for AdminAlertService.

Covers the three behaviours that matter operationally:
- skips when below batch size / empty-ratio threshold
- sends once, then suppresses within cooldown window
- silently no-ops when admin email or Resend key is unset
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.config.settings import Settings
from src.services.alerts import (
    ALERT_AUTH_UNAUTHENTICATED,
    AdminAlertService,
)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        jwt_secret="test-secret-key-for-testing-extended",
        admin_alert_email="admin@example.com",
        resend_api_key="re_test",
        admin_alert_cooldown_hours=12,
        admin_alert_state_path=str(tmp_path / "alerts.json"),
    )


@pytest.fixture
def service(settings):
    return AdminAlertService(settings)


@pytest.mark.asyncio
async def test_below_batch_size_does_not_alert(service):
    with patch.object(service, "_send_email", new=AsyncMock()) as send:
        sent = await service.maybe_alert_unauthenticated_session(
            total_jobs=3, empty_descriptions=3, user_id="u1", search_url=None
        )
    assert sent is False
    send.assert_not_called()


@pytest.mark.asyncio
async def test_below_empty_ratio_does_not_alert(service):
    with patch.object(service, "_send_email", new=AsyncMock()) as send:
        sent = await service.maybe_alert_unauthenticated_session(
            total_jobs=10, empty_descriptions=2, user_id="u1", search_url=None
        )
    assert sent is False
    send.assert_not_called()


@pytest.mark.asyncio
async def test_threshold_crossed_sends_and_persists(service, tmp_path):
    with patch.object(service, "_send_email", new=AsyncMock()) as send:
        sent = await service.maybe_alert_unauthenticated_session(
            total_jobs=40, empty_descriptions=40, user_id="u1",
            search_url="https://linkedin.com/jobs/search/?keywords=python",
        )
    assert sent is True
    send.assert_awaited_once()
    state_path = tmp_path / "alerts.json"
    assert state_path.exists()
    import json
    state = json.loads(state_path.read_text())
    assert ALERT_AUTH_UNAUTHENTICATED in state


@pytest.mark.asyncio
async def test_cooldown_suppresses_second_send(service):
    with patch.object(service, "_send_email", new=AsyncMock()) as send:
        await service.maybe_alert_unauthenticated_session(
            total_jobs=20, empty_descriptions=20, user_id="u1", search_url=None
        )
        sent_again = await service.maybe_alert_unauthenticated_session(
            total_jobs=20, empty_descriptions=20, user_id="u1", search_url=None
        )
    assert sent_again is False
    assert send.await_count == 1


@pytest.mark.asyncio
async def test_cooldown_expires(service, tmp_path):
    import json
    expired = (
        datetime.now(tz=timezone.utc) - timedelta(hours=24)
    ).isoformat()
    state_path = tmp_path / "alerts.json"
    state_path.write_text(json.dumps({ALERT_AUTH_UNAUTHENTICATED: expired}))

    with patch.object(service, "_send_email", new=AsyncMock()) as send:
        sent = await service.maybe_alert_unauthenticated_session(
            total_jobs=20, empty_descriptions=20, user_id="u1", search_url=None
        )
    assert sent is True
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_when_admin_email_missing(tmp_path):
    settings = Settings(
        _env_file=None,
        jwt_secret="test-secret-key-for-testing-extended",
        admin_alert_email="",
        resend_api_key="re_test",
        admin_alert_state_path=str(tmp_path / "alerts.json"),
    )
    service = AdminAlertService(settings)
    assert service.enabled is False
    with patch.object(service, "_send_email", new=AsyncMock()) as send:
        sent = await service.maybe_alert_unauthenticated_session(
            total_jobs=40, empty_descriptions=40, user_id="u1", search_url=None
        )
    assert sent is False
    send.assert_not_called()
