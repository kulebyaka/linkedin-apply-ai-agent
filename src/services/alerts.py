"""Admin operational alerts (Resend-backed, cooldown-throttled).

Fires emails to `settings.admin_alert_email` when the system detects a
condition the administrator needs to act on — currently:

- `auth_session_unauthenticated`: search returned ≥N jobs but ≥50% of detail
  pages had empty descriptions, signalling the LinkedIn `li_at` cookie is
  stale and needs refreshing via the SOCKS-tunnel capture script.

Cooldown state is persisted to a small JSON file so it survives container
restarts (we'd otherwise re-alert on the very next scheduled run).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config.settings import Settings

logger = logging.getLogger(__name__)

ALERT_AUTH_UNAUTHENTICATED = "auth_session_unauthenticated"

# Minimum batch size before we trust the empty-description ratio. Below this,
# transient detail-page failures dominate and the ratio is noisy.
MIN_BATCH_FOR_AUTH_ALERT = 5
# Fraction of empty descriptions that we treat as an unauthenticated signal.
EMPTY_RATIO_THRESHOLD = 0.5


class AdminAlertService:
    """Sends operational alerts to the admin, throttled by a per-key cooldown."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._state_path = Path(settings.admin_alert_state_path)
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self._settings.admin_alert_email and self._settings.resend_api_key)

    async def maybe_alert_unauthenticated_session(
        self,
        *,
        total_jobs: int,
        empty_descriptions: int,
        user_id: str | None,
        search_url: str | None,
    ) -> bool:
        """Fire the auth-session alert if the empty ratio crosses the threshold.

        Returns True if an email was sent, False if skipped (cooldown, below
        threshold, or alerts disabled).
        """
        if total_jobs < MIN_BATCH_FOR_AUTH_ALERT:
            return False
        ratio = empty_descriptions / total_jobs
        if ratio < EMPTY_RATIO_THRESHOLD:
            return False

        subject = "[LinkedIn Apply] LinkedIn session looks unauthenticated"
        html = (
            f"<p>The scheduled LinkedIn search returned <b>{total_jobs}</b> job "
            f"cards but <b>{empty_descriptions}</b> "
            f"({ratio:.0%}) had empty descriptions on the detail page.</p>"
            f"<p>This usually means the <code>li_at</code> cookie has expired "
            f"and the scraper is falling back to an unauthenticated session.</p>"
            f"<p><b>To fix:</b> run the SOCKS-tunnel cookie capture flow from "
            f"the <code>vps</code> skill (see <code>scripts/capture_linkedin_cookies.py</code>), "
            f"then <code>scp</code> the new <code>data/linkedin_cookies.json</code> "
            f"to <code>/opt/linkedin-apply/data/</code> and restart the API.</p>"
            f"<p>User: <code>{user_id or 'global'}</code><br>"
            f"Search URL: {f'<a href={search_url!r}>{search_url}</a>' if search_url else 'n/a'}</p>"
        )
        return await self._send_with_cooldown(
            alert_key=ALERT_AUTH_UNAUTHENTICATED,
            subject=subject,
            html=html,
        )

    async def _send_with_cooldown(
        self, *, alert_key: str, subject: str, html: str
    ) -> bool:
        if not self.enabled:
            logger.debug(
                "Admin alert '%s' suppressed — admin_alert_email or resend_api_key not configured",
                alert_key,
            )
            return False

        async with self._lock:
            state = await asyncio.to_thread(self._read_state)
            last_sent_iso = state.get(alert_key)
            if last_sent_iso:
                try:
                    last_sent = datetime.fromisoformat(last_sent_iso)
                except ValueError:
                    last_sent = None
                if last_sent and datetime.now(tz=timezone.utc) - last_sent < timedelta(
                    hours=self._settings.admin_alert_cooldown_hours
                ):
                    logger.info(
                        "Admin alert '%s' suppressed by cooldown (last sent %s)",
                        alert_key, last_sent_iso,
                    )
                    return False

            try:
                await self._send_email(subject=subject, html=html)
            except Exception:
                logger.exception("Failed to send admin alert '%s'", alert_key)
                return False

            state[alert_key] = datetime.now(tz=timezone.utc).isoformat()
            await asyncio.to_thread(self._write_state, state)
            logger.info(
                "Admin alert '%s' sent to %s", alert_key, self._settings.admin_alert_email
            )
            return True

    async def _send_email(self, *, subject: str, html: str) -> None:
        import resend

        resend.api_key = self._settings.resend_api_key
        params = {
            "from": self._settings.resend_from,
            "to": [self._settings.admin_alert_email],
            "subject": subject,
            "html": html,
        }
        await asyncio.to_thread(resend.Emails.send, params)

    def _read_state(self) -> dict[str, str]:
        if not self._state_path.exists():
            return {}
        try:
            return json.loads(self._state_path.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not read alert state at %s — resetting", self._state_path)
            return {}

    def _write_state(self, state: dict[str, str]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(state, indent=2))
