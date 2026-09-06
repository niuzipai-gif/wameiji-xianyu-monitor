from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from heapq import heappop, heappush

from cd_monitor.core.models import WatchItem


PRIORITY_INTERVALS_MINUTES = {0: (5, 10), 1: (30, 60), 2: (120, 360)}


@dataclass(order=True, slots=True)
class ScheduledWatch:
    due_at: datetime
    watch_item: WatchItem


class WatchPriorityQueue:
    def __init__(self) -> None:
        self._items: list[ScheduledWatch] = []

    def add(self, watch_item: WatchItem, now: datetime | None = None) -> None:
        now = now or datetime.now()
        low, high = PRIORITY_INTERVALS_MINUTES.get(watch_item.priority, PRIORITY_INTERVALS_MINUTES[1])
        interval = int((low + high) / 2)
        heappush(self._items, ScheduledWatch(now + timedelta(minutes=interval), watch_item))

    def pop_due(self, now: datetime | None = None) -> list[WatchItem]:
        now = now or datetime.now()
        due: list[WatchItem] = []
        while self._items and self._items[0].due_at <= now:
            due.append(heappop(self._items).watch_item)
        return due
