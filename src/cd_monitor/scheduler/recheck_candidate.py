from __future__ import annotations

from datetime import datetime, timedelta


def next_recheck_time(now: datetime | None = None, delay_seconds: int = 120) -> datetime:
    delay = min(180, max(60, delay_seconds))
    return (now or datetime.now()) + timedelta(seconds=delay)
