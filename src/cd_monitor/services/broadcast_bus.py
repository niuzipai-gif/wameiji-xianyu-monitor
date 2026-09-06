"""In-process pub/sub for lifecycle / job events (P5.5+).

Used by the Web UI's WebSocket feed. Each subscriber gets its own
``queue.Queue`` so a slow consumer cannot block other subscribers or
the producer. Subscriptions are reference-counted: the same queue can
be subscribed multiple times (useful when the same connection is
re-registered after a reconnect) and ``unsubscribe`` only drops the
last reference.
"""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any, Iterator, Optional
from uuid import uuid4


@dataclass
class _Subscription:
    sid: str
    queue: "queue.Queue[dict[str, Any]]"


class BroadcastBus:
    """Thread-safe, in-process event bus for the Web UI WebSocket feed.

    Events are JSON-serialisable dicts; the WebSocket handler forwards
    them to the client verbatim. Subscribers hold a ``queue.Queue`` that
    is filled by ``publish`` and drained by the handler in a background
    thread.
    """

    def __init__(self, max_queue: int = 256) -> None:
        self._max_queue = max_queue
        self._subs: dict[str, _Subscription] = {}
        self._refcount: dict[str, int] = {}
        self._lock = threading.RLock()
        # monotonic counter for event ordering
        self._counter = 0

    # ---- subscription lifecycle ----

    def subscribe(self) -> str:
        """Register a new subscriber and return its subscription id.

        The id can be passed to ``unsubscribe`` when the WebSocket
        connection closes. A new ``queue.Queue`` is allocated each time
        so independent connections cannot accidentally drain each other.
        """
        sid = uuid4().hex
        with self._lock:
            self._subs[sid] = _Subscription(
                sid=sid,
                queue=queue.Queue(maxsize=self._max_queue),
            )
            self._refcount[sid] = 1
        return sid

    def ref(self, sid: str) -> Optional[str]:
        """Increment the refcount for an existing subscription. Returns
        the sid on success, ``None`` if the sid is unknown (e.g. already
        fully unsubscribed)."""
        with self._lock:
            if sid not in self._subs:
                return None
            self._refcount[sid] = self._refcount.get(sid, 0) + 1
            return sid

    def unsubscribe(self, sid: str) -> bool:
        with self._lock:
            if sid not in self._subs:
                return False
            self._refcount[sid] = self._refcount.get(sid, 1) - 1
            if self._refcount[sid] <= 0:
                self._subs.pop(sid, None)
                self._refcount.pop(sid, None)
        return True

    def queue_for(self, sid: str) -> Optional["queue.Queue[dict[str, Any]]"]:
        with self._lock:
            sub = self._subs.get(sid)
            return sub.queue if sub else None

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    # ---- publishing ----

    def publish(self, event_type: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Publish an event to all current subscribers.

        The event dict is enriched with ``type`` and ``seq`` (monotonic)
        so consumers can order and dispatch on it. Returns the event as
        published; subscribers each receive their own copy.
        """
        with self._lock:
            self._counter += 1
            event = {
                "type": str(event_type),
                "seq": self._counter,
                "payload": dict(payload or {}),
            }
            subs = list(self._subs.values())
        # Fan-out outside the lock; never block a slow consumer.
        for sub in subs:
            try:
                sub.queue.put_nowait(event)
            except queue.Full:
                # Drop on the floor. The consumer is too slow; we'd
                # rather lose events than block the producer.
                pass
        return event

    def drain(self, sid: str, *, block: bool = True, timeout: Optional[float] = None) -> Iterator[dict[str, Any]]:
        """Yield events for ``sid`` until the subscription is closed.

        ``timeout=None`` blocks forever; ``timeout=0`` is non-blocking.
        """
        q = self.queue_for(sid)
        if q is None:
            return
        while True:
            try:
                event = q.get(block=block, timeout=timeout)
            except queue.Empty:
                return
            yield event
