"""兼容层：re-export notification_service.build_notification_service。"""
from cd_monitor.services.scraper._notification_service import (
    NotificationService,
    build_notification_service,
)

__all__ = ["NotificationService", "build_notification_service"]
