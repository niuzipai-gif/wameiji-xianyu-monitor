"""Tests for the P0 #11 broadcast bus + WebSocket lifecycle hook."""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from cd_monitor.core.models import WatchItem
from cd_monitor.services.broadcast_bus import BroadcastBus
from cd_monitor.services.watch_action_service import WatchActionService
from cd_monitor.storage.sqlite import add_watch, init_db
from cd_monitor.web_server import _build_handler, _get_broadcast_bus


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "monitor.db"
    init_db(str(db))
    return db


# -------- BroadcastBus unit tests --------


def test_subscribe_and_publish() -> None:
    bus = BroadcastBus()
    sid = bus.subscribe()
    try:
        evt = bus.publish("job_started", {"watch_id": 1})
        assert evt["type"] == "job_started"
        assert evt["seq"] == 1
        assert evt["payload"] == {"watch_id": 1}
        q = bus.queue_for(sid)
        assert q is not None
        assert q.get_nowait() == evt
    finally:
        bus.unsubscribe(sid)


def test_each_subscriber_gets_independent_queue() -> None:
    bus = BroadcastBus()
    sid_a = bus.subscribe()
    sid_b = bus.subscribe()
    try:
        bus.publish("ping", {"a": 1})
        bus.publish("ping", {"a": 2})
        qa = bus.queue_for(sid_a)
        qb = bus.queue_for(sid_b)
        assert qa is not None and qb is not None
        assert qa.qsize() == 2
        assert qb.qsize() == 2
        assert qa.get_nowait()["payload"] == {"a": 1}
        assert qb.get_nowait()["payload"] == {"a": 1}
    finally:
        bus.unsubscribe(sid_a)
        bus.unsubscribe(sid_b)


def test_unsubscribe_is_reference_counted() -> None:
    bus = BroadcastBus()
    sid = bus.subscribe()
    assert bus.ref(sid) == sid
    assert bus.ref(sid) == sid
    bus.unsubscribe(sid)
    # Two refs still alive -> queue is still there
    assert bus.queue_for(sid) is not None
    bus.unsubscribe(sid)
    assert bus.queue_for(sid) is not None
    bus.unsubscribe(sid)
    # Refcount hit zero on the third unsubscribe -> queue gone.
    assert bus.queue_for(sid) is None


def test_publish_does_not_block_when_subscriber_is_slow() -> None:
    bus = BroadcastBus(max_queue=2)
    sid = bus.subscribe()
    try:
        # Fill the queue and confirm publish keeps returning.
        bus.publish("a")
        bus.publish("a")
        # Queue is full now; publish must not block.
        t0 = time.monotonic()
        bus.publish("a")
        assert time.monotonic() - t0 < 0.5
    finally:
        bus.unsubscribe(sid)


def test_publish_seq_is_monotonic() -> None:
    bus = BroadcastBus()
    sid = bus.subscribe()
    try:
        for _ in range(5):
            bus.publish("x")
        q = bus.queue_for(sid)
        assert q is not None
        seqs = [q.get_nowait()["seq"] for _ in range(5)]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == 5
    finally:
        bus.unsubscribe(sid)


# -------- WatchActionService integration --------


def test_watch_action_service_emits_lifecycle_events(tmp_path: Path, monkeypatch) -> None:
    db = _make_db(tmp_path)
    wid = add_watch(str(db), WatchItem(catalog_no="W-1", title_jp="x"))

    bus = _get_broadcast_bus(str(db))
    # Wipe any leftover events from earlier tests sharing the singleton.
    sid = bus.subscribe()
    while bus.queue_for(sid) is not None:
        try:
            bus.queue_for(sid).get_nowait()
        except Exception:
            break
    bus.unsubscribe(sid)

    # Submit a run job and capture emitted events.
    sid = bus.subscribe()
    try:
        svc = WatchActionService(str(db))
        job = svc.submit_run(wid)
        # Drain with a deadline; the worker should run quickly.
        deadline = time.monotonic() + 5.0
        events = []
        q = bus.queue_for(sid)
        assert q is not None
        while time.monotonic() < deadline:
            try:
                events.append(q.get(timeout=0.1))
            except Exception:
                if job.status in {"completed", "failed", "cancelled"}:
                    break
        types = [e["type"] for e in events]
        # Must include at least one ``watch_action`` event with the job's status changes.
        assert any("watch_action" == t for t in types)
        # And a ``watch_action_terminal`` once the job reached a terminal state.
        assert any("watch_action_terminal" == t for t in types)
    finally:
        bus.unsubscribe(sid)


# -------- WebSocket integration --------


def _http_request(base, path, payload=None, method="POST"):
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.getcode(), resp
    except urllib.error.HTTPError as exc:
        return exc.code, exc


def _ws_connect(host: str, port: int, path: str = "/ws") -> tuple[socket.socket, threading.Thread]:
    """Open a raw TCP socket and perform the RFC 6455 client handshake."""
    import base64
    import os
    s = socket.create_connection((host, port), timeout=5.0)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    s.sendall(req.encode("ascii"))
    # Read until the end of the headers.
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    head, _, _rest = buf.partition(b"\r\n\r\n")
    head_text = head.decode("latin-1", errors="replace")
    assert "101" in head_text.split("\r\n", 1)[0], head_text
    assert "Sec-WebSocket-Accept" in head_text, head_text
    return s, threading.Thread()


def _ws_read_frame(sock: socket.socket, timeout: float = 5.0) -> dict | None:
    """Read text frames from ``sock`` until we get a JSON text frame, the
    peer closes, or the read times out. Non-text frames (ping/pong/close)
    are skipped; the buffer can carry multiple frames at once."""
    sock.settimeout(timeout)
    buffer = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # If the buffer is empty, block for a chunk. Otherwise keep
        # parsing whatever is already buffered without blocking.
        if not buffer:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                return None
            if not chunk:
                return None
            buffer = chunk
        if len(buffer) < 2:
            buffer += sock.recv(2 - len(buffer))
        b1, b2 = buffer[0], buffer[1]
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        idx = 2
        if length == 126:
            if len(buffer) < idx + 2:
                buffer += sock.recv(idx + 2 - len(buffer))
            length = int.from_bytes(buffer[idx:idx + 2], "big")
            idx += 2
        elif length == 127:
            if len(buffer) < idx + 8:
                buffer += sock.recv(idx + 8 - len(buffer))
            length = int.from_bytes(buffer[idx:idx + 8], "big")
            idx += 8
        if masked:
            if len(buffer) < idx + 4:
                buffer += sock.recv(idx + 4 - len(buffer))
            mask = buffer[idx:idx + 4]
            idx += 4
        else:
            mask = b""
        if len(buffer) < idx + length:
            buffer += sock.recv(idx + length - len(buffer))
        payload = buffer[idx:idx + length]
        buffer = buffer[idx + length:]
        if masked and payload:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode == 0x8:  # close
            return None
        if opcode == 0x1:  # text
            return json.loads(payload.decode("utf-8"))
        # ping / pong / binary: skip and keep draining
    return None


@pytest.fixture
def http_server(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.chdir(tmp_path)
    handler = _build_handler(db_path=db, static_dir=tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, db
    server.shutdown()
    server.server_close()


def test_websocket_receives_lifecycle_event(http_server) -> None:
    import base64
    import os
    server, db = http_server
    wid = add_watch(str(db), WatchItem(catalog_no="WS-1", title_jp="x"))
    host, port = server.server_address

    s, _ = _ws_connect(host, port)
    try:
        # Trigger an event: submit a manual run.
        svc = WatchActionService(str(db))
        svc.submit_run(wid)
        # Read at least one event off the socket.
        deadline = time.monotonic() + 5.0
        received = None
        while time.monotonic() < deadline:
            evt = _ws_read_frame(s, timeout=0.5)
            if evt is not None and evt.get("type", "").startswith("watch_action"):
                received = evt
                break
        assert received is not None, "no watch_action event received over WS"
        assert received["payload"]["watch_id"] == wid
    finally:
        s.close()
