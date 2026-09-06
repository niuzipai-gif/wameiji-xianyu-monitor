"""Cron parsing and validation utilities."""
from __future__ import annotations

from typing import Optional

from apscheduler.triggers.cron import CronTrigger

CRON_ALIASES = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

CRON_FORMAT_HINT = (
    "Cron expression invalid. Supports 5-field (m h d M w), 6-field (s m h d M w), "
    "and common aliases (@hourly/@daily/@weekly/@monthly/@yearly). "
    "Examples: */15 * * * *, 0 8 * * *, 0 0 8 * * *, @daily."
)


def normalize_cron_expression(value):
    if value is None:
        return None
    normalized = " ".join(str(value).strip().split())
    if not normalized:
        return None
    return CRON_ALIASES.get(normalized.lower(), normalized)


def build_cron_trigger(expression, *, timezone=None):
    normalized = normalize_cron_expression(expression)
    if normalized is None:
        raise ValueError(CRON_FORMAT_HINT)
    parts = normalized.split()
    try:
        if len(parts) == 5:
            return CronTrigger.from_crontab(normalized, timezone=timezone)
        if len(parts) == 6:
            second, minute, hour, day, month, day_of_week = parts
            return CronTrigger(
                second=second,
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=day_of_week,
                timezone=timezone,
            )
    except ValueError as exc:
        raise ValueError(CRON_FORMAT_HINT) from exc
    raise ValueError(CRON_FORMAT_HINT)


def validate_cron_expression(value):
    normalized = normalize_cron_expression(value)
    if normalized is None:
        return None
    build_cron_trigger(normalized)
    return normalized

