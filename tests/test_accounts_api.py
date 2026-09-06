"""Tests for /api/accounts CRUD endpoints (multi-account support, P5.2+).

Covers:
- GET /api/accounts (list all)
- POST /api/accounts (create with name + platform + state_payload)
- POST /api/accounts/{id} (update content / notes / enabled)
- GET /api/accounts/{id} (fetch one + state)
- DELETE /api/accounts/{id}/delete
- File isolation: each account has its own JSON file under
  <account_dir>/<platform>/<name>.json
- AccountRepository CRUD at the service layer.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from cd_monitor.services.account_repository import (
    AccountError,
    AccountRepository,
)
from cd_monitor.storage.sqlite import init_db
from cd_monitor.web_server import create_server


def _post_json(url, body):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req)
        return resp.getcode(), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _delete(url):
    req = urllib.request.Request(url, method="DELETE")
    resp = urllib.request.urlopen(req)
    return resp.getcode(), json.loads(resp.read().decode("utf-8"))


def _get(url):
    return json.loads(urllib.request.urlopen(url).read().decode("utf-8"))


def _start_server(tmp_path):
    db_path = tmp_path / "ls.db"
    init_db(db_path)
    account_dir = tmp_path / "accounts"
    server = create_server(
        "127.0.0.1", 0, db_path,
        static_dir="web",
        account_dir=str(account_dir),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    return server, thread, base_url, account_dir


W_J = {"playwright_storage_state": {"cookies": [{"name": "sid", "value": "w", "domain": ".meruki.cn", "expires": -1}], "origins": []}}
X_J = {"playwright_storage_state": {"cookies": [{"name": "sid", "value": "x", "domain": ".goofish.com", "expires": -1}], "origins": []}}


class TestAccountRepositoryService:
    def test_create_list_get(self, tmp_path):
        repo = AccountRepository(tmp_path / "db", account_dir=tmp_path / "acc")
        repo.create("alpha", "wameiji", W_J)
        repo.create("beta", "xianyu", X_J)
        items = repo.list_all()
        assert len(items) == 2
        names = {(it["platform"], it["name"]) for it in items}
        assert {("wameiji", "alpha"), ("xianyu", "beta")} <= names

    def test_create_writes_state_file(self, tmp_path):
        repo = AccountRepository(tmp_path / "db", account_dir=tmp_path / "acc")
        repo.create("a1", "wameiji", W_J)
        path = tmp_path / "acc" / "wameiji" / "a1.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == W_J

    def test_create_duplicate_raises(self, tmp_path):
        repo = AccountRepository(tmp_path / "db", account_dir=tmp_path / "acc")
        repo.create("dup", "wameiji", W_J)
        try:
            repo.create("dup", "wameiji", W_J)
        except AccountError:
            return
        raise AssertionError("expected AccountError on duplicate")

    def test_create_invalid_name_raises(self, tmp_path):
        repo = AccountRepository(tmp_path / "db", account_dir=tmp_path / "acc")
        for bad in ("", "  ", "with space", "a" * 60, "../escape"):
            try:
                repo.create(bad, "wameiji", W_J)
            except AccountError:
                continue
            raise AssertionError(f"expected AccountError for name={bad!r}")

    def test_create_invalid_platform_raises(self, tmp_path):
        repo = AccountRepository(tmp_path / "db", account_dir=tmp_path / "acc")
        try:
            repo.create("ok", "ebay", W_J)
        except AccountError:
            return
        raise AssertionError("expected AccountError for invalid platform")

    def test_update_content(self, tmp_path):
        repo = AccountRepository(tmp_path / "db", account_dir=tmp_path / "acc")
        account = repo.create("upd", "wameiji", W_J)
        new_payload = {"cookies": [{"name": "new", "value": "v", "domain": ".meruki.cn"}], "origins": []}
        repo.update_content(account["id"], new_payload)
        path = tmp_path / "acc" / "wameiji" / "upd.json"
        assert json.loads(path.read_text(encoding="utf-8")) == new_payload

    def test_update_meta(self, tmp_path):
        repo = AccountRepository(tmp_path / "db", account_dir=tmp_path / "acc")
        account = repo.create("m", "xianyu", X_J)
        updated = repo.update_meta(account["id"], notes="primary", enabled=False)
        assert updated["notes"] == "primary"
        assert updated["enabled"] is False

    def test_delete_removes_file_and_row(self, tmp_path):
        repo = AccountRepository(tmp_path / "db", account_dir=tmp_path / "acc")
        account = repo.create("rm", "wameiji", W_J)
        path = tmp_path / "acc" / "wameiji" / "rm.json"
        assert path.exists()
        assert repo.delete(account["id"]) is True
        assert not path.exists()
        assert repo.get(account["id"]) is None

    def test_delete_unknown_returns_false(self, tmp_path):
        repo = AccountRepository(tmp_path / "db", account_dir=tmp_path / "acc")
        assert repo.delete(9999) is False

    def test_list_filter_by_platform(self, tmp_path):
        repo = AccountRepository(tmp_path / "db", account_dir=tmp_path / "acc")
        repo.create("a", "wameiji", W_J)
        repo.create("b", "xianyu", X_J)
        repo.create("c", "wameiji", W_J)
        w = repo.list_all(platform="wameiji")
        assert len(w) == 2 and all(it["platform"] == "wameiji" for it in w)

    def test_state_status(self, tmp_path):
        repo = AccountRepository(tmp_path / "db", account_dir=tmp_path / "acc")
        account = repo.create("s", "wameiji", W_J)
        assert repo.state_status(account["id"])["status"] == "ready"
        # Corrupt the file
        path = tmp_path / "acc" / "wameiji" / "s.json"
        path.write_text("not json", encoding="utf-8")
        assert repo.state_status(account["id"])["status"] == "invalid"
        path.unlink()
        assert repo.state_status(account["id"])["status"] == "missing"

    def test_state_status_unknown_id(self, tmp_path):
        repo = AccountRepository(tmp_path / "db", account_dir=tmp_path / "acc")
        assert repo.state_status(9999)["status"] == "not_found"


class TestAccountsApi:
    def test_list_empty(self, tmp_path):
        server, thread, base, _ = _start_server(tmp_path)
        try:
            body = _get(f"{base}/api/accounts")
            assert body == {"items": []}
        finally:
            server.shutdown(); server.server_close()

    def test_create_then_list(self, tmp_path):
        server, thread, base, _ = _start_server(tmp_path)
        try:
            code, body = _post_json(f"{base}/api/accounts", {
                "name": "alpha",
                "platform": "wameiji",
                "state_payload": W_J,
            })
            assert code == 200
            assert body["created"] is True
            assert body["account"]["name"] == "alpha"
            items = _get(f"{base}/api/accounts")["items"]
            assert len(items) == 1
            assert items[0]["platform"] == "wameiji"
        finally:
            server.shutdown(); server.server_close()

    def test_create_with_snapshot_string(self, tmp_path):
        server, thread, base, _ = _start_server(tmp_path)
        try:
            code, body = _post_json(f"{base}/api/accounts", {
                "name": "beta",
                "platform": "xianyu",
                "snapshot": json.dumps(X_J),
            })
            assert code == 200
            assert body["account"]["name"] == "beta"
        finally:
            server.shutdown(); server.server_close()

    def test_create_missing_fields_returns_400(self, tmp_path):
        server, thread, base, _ = _start_server(tmp_path)
        try:
            code, body = _post_json(f"{base}/api/accounts", {"name": "x"})
            assert code == 400
            assert body["error"] == "name_platform_payload_required"
        finally:
            server.shutdown(); server.server_close()

    def test_create_invalid_platform_returns_400(self, tmp_path):
        server, thread, base, _ = _start_server(tmp_path)
        try:
            code, body = _post_json(f"{base}/api/accounts", {
                "name": "x", "platform": "ebay", "state_payload": W_J,
            })
            assert code == 400
            assert body["error"] == "create_failed"
        finally:
            server.shutdown(); server.server_close()

    def test_create_duplicate_returns_400(self, tmp_path):
        server, thread, base, _ = _start_server(tmp_path)
        try:
            _post_json(f"{base}/api/accounts", {
                "name": "dup", "platform": "wameiji", "state_payload": W_J,
            })
            code, body = _post_json(f"{base}/api/accounts", {
                "name": "dup", "platform": "wameiji", "state_payload": W_J,
            })
            assert code == 400
            assert "already exists" in body["detail"]
        finally:
            server.shutdown(); server.server_close()

    def test_create_writes_state_file(self, tmp_path):
        server, thread, base, acc_dir = _start_server(tmp_path)
        try:
            _post_json(f"{base}/api/accounts", {
                "name": "fs", "platform": "wameiji", "state_payload": W_J,
            })
            path = acc_dir / "wameiji" / "fs.json"
            assert path.exists()
            assert json.loads(path.read_text(encoding="utf-8")) == W_J
        finally:
            server.shutdown(); server.server_close()

    def test_get_by_id(self, tmp_path):
        server, thread, base, _ = _start_server(tmp_path)
        try:
            _, body = _post_json(f"{base}/api/accounts", {
                "name": "g", "platform": "xianyu", "state_payload": X_J,
            })
            account_id = body["account"]["id"]
            detail = _get(f"{base}/api/accounts/{account_id}")
            assert detail["account"]["name"] == "g"
            assert detail["state"] == X_J
        finally:
            server.shutdown(); server.server_close()

    def test_get_unknown_returns_404(self, tmp_path):
        server, thread, base, _ = _start_server(tmp_path)
        try:
            try:
                _get(f"{base}/api/accounts/99999")
            except urllib.error.HTTPError as exc:
                assert exc.code == 404
                return
            raise AssertionError("expected 404")
        finally:
            server.shutdown(); server.server_close()

    def test_status_endpoint(self, tmp_path):
        server, thread, base, _ = _start_server(tmp_path)
        try:
            _, body = _post_json(f"{base}/api/accounts", {
                "name": "st", "platform": "wameiji", "state_payload": W_J,
            })
            account_id = body["account"]["id"]
            status = _get(f"{base}/api/accounts/{account_id}/status")
            assert status["state"]["status"] == "ready"
        finally:
            server.shutdown(); server.server_close()

    def test_update_content_via_post(self, tmp_path):
        server, thread, base, _ = _start_server(tmp_path)
        try:
            _, body = _post_json(f"{base}/api/accounts", {
                "name": "u", "platform": "wameiji", "state_payload": W_J,
            })
            account_id = body["account"]["id"]
            new_payload = {"cookies": [{"name": "new", "value": "v", "domain": ".meruki.cn"}], "origins": []}
            code, body = _post_json(f"{base}/api/accounts/{account_id}", {
                "state_payload": new_payload,
                "notes": "rotated",
            })
            assert code == 200
            assert body["account"]["notes"] == "rotated"
            detail = _get(f"{base}/api/accounts/{account_id}")
            assert detail["state"] == new_payload
        finally:
            server.shutdown(); server.server_close()

    def test_update_unknown_returns_404(self, tmp_path):
        server, thread, base, _ = _start_server(tmp_path)
        try:
            code, body = _post_json(f"{base}/api/accounts/99999", {"notes": "x"})
            assert code == 404
        finally:
            server.shutdown(); server.server_close()

    def test_delete_account(self, tmp_path):
        server, thread, base, acc_dir = _start_server(tmp_path)
        try:
            _, body = _post_json(f"{base}/api/accounts", {
                "name": "del", "platform": "wameiji", "state_payload": W_J,
            })
            account_id = body["account"]["id"]
            path = acc_dir / "wameiji" / "del.json"
            assert path.exists()
            code, body = _delete(f"{base}/api/accounts/{account_id}/delete")
            assert code == 200
            assert body["deleted"] is True
            assert not path.exists()
            assert _get(f"{base}/api/accounts")["items"] == []
        finally:
            server.shutdown(); server.server_close()

    def test_delete_unknown_returns_200_false(self, tmp_path):
        server, thread, base, _ = _start_server(tmp_path)
        try:
            code, body = _delete(f"{base}/api/accounts/99999/delete")
            assert code == 200
            assert body["deleted"] is False
        finally:
            server.shutdown(); server.server_close()

    def test_file_isolation_between_accounts(self, tmp_path):
        server, thread, base, acc_dir = _start_server(tmp_path)
        try:
            _post_json(f"{base}/api/accounts", {
                "name": "acc1", "platform": "wameiji", "state_payload": W_J,
            })
            _post_json(f"{base}/api/accounts", {
                "name": "acc2", "platform": "wameiji", "state_payload": W_J,
            })
            f1 = acc_dir / "wameiji" / "acc1.json"
            f2 = acc_dir / "wameiji" / "acc2.json"
            assert f1.exists() and f2.exists()
            assert f1 != f2
        finally:
            server.shutdown(); server.server_close()