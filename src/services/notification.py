"""Service for sending notifications"""

from enum import Enum


class NotificationType(str, Enum):
    """Types of notifications"""

    ERROR = "error"
    SUCCESS = "success"
    APPROVAL_NEEDED = "approval_needed"


class NotificationService:
    """Sends notifications via webhook or other channels"""

    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url

    def send(
        self, notification_type: NotificationType, message: str, data: dict | None = None
    ) -> bool:
        """
        Send a notification

        Args:
            notification_type: Type of notification
            message: Notification message
            data: Additional data to include

        Returns:
            Success status
        """
        # TODO: Implement notification sending
        # Options:
        # 1. Webhook (Discord, Slack, custom endpoint)
        # 2. Email
        # 3. Push notification

        raise NotImplementedError

    def send_error(self, error_message: str, context: dict | None = None) -> bool:
        """Send error notification"""
        return self.send(NotificationType.ERROR, error_message, context)

    def send_success(self, message: str, context: dict | None = None) -> bool:
        """Send success notification"""
        return self.send(NotificationType.SUCCESS, message, context)

    def send_approval_request(self, job_data: dict, cv_path: str) -> bool:
        """Send notification requesting user approval"""
        return self.send(
            NotificationType.APPROVAL_NEEDED,
            "New job application ready for review",
            {"job": job_data, "cv_path": cv_path},
        )
