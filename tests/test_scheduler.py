from datetime import datetime, timedelta

from cd_monitor.core.models import WatchItem
from cd_monitor.scheduler.priority_queue import WatchPriorityQueue
from cd_monitor.scheduler.recheck_candidate import next_recheck_time


def test_priority_queue_schedules_and_pops_due_watch_items() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0)
    queue = WatchPriorityQueue()
    queue.add(WatchItem(catalog_no="P0", priority=0), now=now)
    queue.add(WatchItem(catalog_no="P1", priority=1), now=now)

    assert queue.pop_due(now + timedelta(minutes=7)) == [WatchItem(catalog_no="P0", priority=0)]
    assert queue.pop_due(now + timedelta(minutes=45)) == [WatchItem(catalog_no="P1", priority=1)]


def test_recheck_time_clamps_to_sixty_to_one_eighty_seconds() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0)

    assert next_recheck_time(now, delay_seconds=1) == now + timedelta(seconds=60)
    assert next_recheck_time(now, delay_seconds=999) == now + timedelta(seconds=180)
