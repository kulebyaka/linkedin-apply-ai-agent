"""Tests for LinkedInAutomation stealth browser manager."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.browser_automation import LinkedInAutomation

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_settings(tmp_path):
    """Create a mock Settings object with test values."""
    settings = MagicMock()
    settings.linkedin_email = "test@example.com"
    settings.linkedin_password = "secret123"
    settings.browser_headless = True
    settings.linkedin_session_cookie_path = str(tmp_path / "cookies.json")
    settings.linkedin_min_delay = 0.01
    settings.linkedin_max_delay = 0.02
    settings.linkedin_page_delay_min = 0.01
    settings.linkedin_page_delay_max = 0.02
    return settings


@pytest.fixture
def automation(mock_settings):
    """Create a LinkedInAutomation instance with mock settings."""
    return LinkedInAutomation(mock_settings)


class TestInit:
    def test_stores_settings(self, automation, mock_settings):
        assert automation.email == "test@example.com"
        assert automation.password == "secret123"
        assert automation.headless is True
        assert automation.min_delay == 0.01
        assert automation.max_delay == 0.02

    def test_cookie_path_is_pathlib(self, automation):
        assert isinstance(automation.cookie_path, Path)


class TestLoadCookies:
    async def test_returns_false_when_no_file(self, automation):
        result = await automation._load_cookies()
        assert result is False

    async def test_loads_cookies_from_file(self, automation, tmp_path):
        cookies = [{"name": "li_at", "value": "abc123", "domain": ".linkedin.com", "path": "/"}]
        cookie_file = Path(automation.cookie_path)
        cookie_file.parent.mkdir(parents=True, exist_ok=True)
        cookie_file.write_text(json.dumps(cookies))

        # Mock context
        automation.context = AsyncMock()
        result = await automation._load_cookies()
        assert result is True
        automation.context.add_cookies.assert_awaited_once_with(cookies)

    async def test_returns_false_on_empty_cookies(self, automation):
        cookie_file = Path(automation.cookie_path)
        cookie_file.parent.mkdir(parents=True, exist_ok=True)
        cookie_file.write_text("[]")

        automation.context = AsyncMock()
        result = await automation._load_cookies()
        assert result is False

    async def test_returns_false_on_invalid_json(self, automation):
        cookie_file = Path(automation.cookie_path)
        cookie_file.parent.mkdir(parents=True, exist_ok=True)
        cookie_file.write_text("not json")

        automation.context = AsyncMock()
        result = await automation._load_cookies()
        assert result is False


class TestSaveCookies:
    async def test_saves_cookies_to_file(self, automation):
        cookies = [{"name": "li_at", "value": "xyz789", "domain": ".linkedin.com", "path": "/"}]
        automation.context = AsyncMock()
        automation.context.cookies = AsyncMock(return_value=cookies)

        await automation._save_cookies()

        cookie_file = Path(automation.cookie_path)
        assert cookie_file.exists()
        saved = json.loads(cookie_file.read_text())
        assert saved == cookies

    async def test_creates_parent_directories(self, automation, tmp_path):
        automation.cookie_path = tmp_path / "deep" / "nested" / "cookies.json"
        automation.context = AsyncMock()
        automation.context.cookies = AsyncMock(return_value=[])

        await automation._save_cookies()
        assert automation.cookie_path.exists()


class TestValidateSession:
    async def test_valid_session(self, automation):
        automation.page = AsyncMock()
        automation.page.goto = AsyncMock()
        automation.page.url = "https://www.linkedin.com/feed/"

        result = await automation._validate_session()
        assert result is True
        automation.page.goto.assert_awaited_once()

    async def test_invalid_session_redirected_to_login(self, automation):
        automation.page = AsyncMock()
        automation.page.goto = AsyncMock()
        automation.page.url = "https://www.linkedin.com/login"

        result = await automation._validate_session()
        assert result is False

    async def test_handles_navigation_error(self, automation):
        automation.page = AsyncMock()
        automation.page.goto = AsyncMock(side_effect=Exception("Network error"))

        result = await automation._validate_session()
        assert result is False


class TestEnsureAuthenticated:
    async def test_reuses_cookies_when_valid(self, automation):
        automation._load_cookies = AsyncMock(return_value=True)
        automation._validate_session = AsyncMock(return_value=True)
        automation.login = AsyncMock()
        automation._save_cookies = AsyncMock()

        await automation.ensure_authenticated()

        automation._load_cookies.assert_awaited_once()
        automation._validate_session.assert_awaited_once()
        automation.login.assert_not_awaited()

    async def test_falls_back_to_login_when_cookies_invalid(self, automation):
        automation._load_cookies = AsyncMock(return_value=True)
        automation._validate_session = AsyncMock(return_value=False)
        automation.login = AsyncMock()

        await automation.ensure_authenticated()

        automation.login.assert_awaited_once()

    async def test_falls_back_to_login_when_no_cookies(self, automation):
        automation._load_cookies = AsyncMock(return_value=False)
        automation._validate_session = AsyncMock()
        automation.login = AsyncMock()

        await automation.ensure_authenticated()

        automation._validate_session.assert_not_awaited()
        automation.login.assert_awaited_once()


class TestRandomDelay:
    async def test_uses_default_delays(self, automation):
        with patch("src.services.browser_automation.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await automation.random_delay()
            mock_sleep.assert_awaited_once()
            delay = mock_sleep.call_args[0][0]
            assert automation.min_delay <= delay <= automation.max_delay

    async def test_uses_custom_delays(self, automation):
        with patch("src.services.browser_automation.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await automation.random_delay(min_s=1.0, max_s=2.0)
            delay = mock_sleep.call_args[0][0]
            assert 1.0 <= delay <= 2.0


class TestHumanScroll:
    async def test_scrolls_multiple_times(self, automation):
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock()

        with patch("src.services.browser_automation.random.randint", return_value=3):
            with patch("src.services.browser_automation.asyncio.sleep", new_callable=AsyncMock):
                await automation.human_scroll(mock_page)

        assert mock_page.evaluate.await_count == 3

    async def test_uses_self_page_when_none_passed(self, automation):
        automation.page = AsyncMock()
        automation.page.evaluate = AsyncMock()

        with patch("src.services.browser_automation.random.randint", return_value=2):
            with patch("src.services.browser_automation.asyncio.sleep", new_callable=AsyncMock):
                await automation.human_scroll()

        assert automation.page.evaluate.await_count == 2


class TestClose:
    async def test_closes_browser_and_playwright(self, automation):
        mock_context = AsyncMock()
        mock_context.cookies = AsyncMock(return_value=[])
        mock_browser = AsyncMock()
        mock_pw = AsyncMock()

        automation.context = mock_context
        automation.browser = mock_browser
        automation._playwright = mock_pw

        await automation.close()

        mock_browser.close.assert_awaited_once()
        mock_pw.stop.assert_awaited_once()
        assert automation.browser is None
        assert automation._playwright is None

    async def test_handles_no_browser(self, automation):
        automation.context = None
        automation.browser = None
        automation._playwright = None

        # Should not raise
        await automation.close()


class TestLogin:
    async def test_login_fills_credentials_and_submits(self, automation):
        mock_email_input = AsyncMock()
        mock_password_input = AsyncMock()
        mock_submit_btn = AsyncMock()

        mock_page = AsyncMock()
        # locator() is synchronous in Playwright - use MagicMock side_effect
        mock_page.locator = MagicMock(side_effect=lambda selector: {
            'input#username': mock_email_input,
            'input#password': mock_password_input,
            'button[type="submit"]': mock_submit_btn,
        }[selector])
        mock_page.url = "https://www.linkedin.com/feed/"

        automation.page = mock_page
        automation.context = AsyncMock()
        automation.context.cookies = AsyncMock(return_value=[])
        automation.random_delay = AsyncMock()

        await automation.login()

        mock_email_input.click.assert_awaited_once()
        mock_email_input.type.assert_awaited_once()
        assert mock_email_input.type.call_args[0][0] == "test@example.com"

        mock_password_input.click.assert_awaited_once()
        mock_password_input.type.assert_awaited_once()
        assert mock_password_input.type.call_args[0][0] == "secret123"

        mock_submit_btn.click.assert_awaited_once()

    async def test_login_detects_security_challenge(self, automation):
        mock_input = AsyncMock()

        mock_page = AsyncMock()
        mock_page.locator = MagicMock(return_value=mock_input)
        mock_page.url = "https://www.linkedin.com/checkpoint/challenge/123"

        automation.page = mock_page
        automation.context = AsyncMock()
        automation.context.cookies = AsyncMock(return_value=[])
        automation.random_delay = AsyncMock()

        # Should raise RuntimeError on failed login (security challenge URL)
        with pytest.raises(RuntimeError, match="LinkedIn login failed"):
            await automation.login()
