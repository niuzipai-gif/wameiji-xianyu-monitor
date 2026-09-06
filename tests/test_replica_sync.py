from __future__ import annotations

import gzip
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from cd_monitor.web_server import create_server
from cd_monitor.storage.sqlite import init_db


def _post(url: str, body: bytes, headers: dict[str, str]):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    return urllib.request.urlopen(req, timeout=10)


def test_database_replica_requires_token_and_replaces_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("CD_SYNC_TOKEN", "replica-secret")
    remote = tmp_path / "remote.db"
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        conn.execute("INSERT INTO marker(value) VALUES ('published')")
    server = create_server("127.0.0.1", 0, remote, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        body = gzip.compress(source.read_bytes())
        try:
            _post(
                f"{base}/api/sync/database",
                body,
                {"Content-Encoding": "gzip", "Content-Type": "application/octet-stream"},
            )
        except urllib.error.HTTPError as exc:
            try:
                assert exc.code == 401
                assert json.loads(exc.read().decode("utf-8"))["error"] == "sync_unauthorized"
            finally:
                exc.close()
        else:
            raise AssertionError("replica endpoint accepted a missing token")

        try:
            with _post(
                f"{base}/api/sync/database",
                body,
                {
                    "Content-Encoding": "gzip",
                    "Content-Type": "application/octet-stream",
                    "X-CD-Sync-Token": "replica-secret",
                },
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                raise AssertionError(exc.read().decode("utf-8")) from exc
            finally:
                exc.close()
        assert payload["ok"] is True
        with sqlite3.connect(remote) as conn:
            assert conn.execute("SELECT value FROM marker").fetchone()[0] == "published"
    finally:
        server.shutdown()
        server.server_close()


def test_pending_remote_command_survives_replica_database_upload(tmp_path, monkeypatch):
    """Replication must never erase a command that the collector has not seen."""
    monkeypatch.setenv("WEB_ACCESS_TOKEN", "viewer-secret")
    monkeypatch.setenv("CD_SYNC_TOKEN", "replica-secret")
    remote = tmp_path / "remote.db"
    command_db = tmp_path / "remote-commands.db"
    source = tmp_path / "source.db"
    monkeypatch.setenv("CD_COMMAND_DB_PATH", str(command_db))
    init_db(remote)
    init_db(source)

    server = create_server("127.0.0.1", 0, remote, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        body = json.dumps({"command_type": "scan_now"}).encode("utf-8")
        request = urllib.request.Request(
            f"{base}/api/discovery/commands?access_token=viewer-secret",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            command = json.loads(response.read().decode("utf-8"))
        assert command["status"] == "pending"

        replicated = gzip.compress(source.read_bytes())
        with _post(
            f"{base}/api/sync/database",
            replicated,
            {
                "Content-Encoding": "gzip",
                "Content-Type": "application/octet-stream",
                "X-CD-Sync-Token": "replica-secret",
            },
        ) as response:
            assert json.loads(response.read().decode("utf-8"))["ok"] is True

        queued = urllib.request.Request(
            f"{base}/api/discovery/collector/commands",
            headers={"X-CD-Sync-Token": "replica-secret"},
            method="GET",
        )
        with urllib.request.urlopen(queued, timeout=10) as response:
            items = json.loads(response.read().decode("utf-8"))["items"]
        assert [item["id"] for item in items] == [command["id"]]
    finally:
        server.shutdown()
        server.server_close()
