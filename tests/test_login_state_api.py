"""Tests for /api/login-state/{xianyu,wameiji} POST and DELETE endpoints."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

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


def _start_server(tmp_path, xianyu_file, wameiji_file):
    db_path = tmp_path / "ls.db"
    init_db(db_path)
    server = create_server(
        "127.0.0.1", 0, db_path,
        static_dir="web",
        xianyu_state_file=str(xianyu_file),
        wameiji_state_file=str(wameiji_file),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    return server, thread, base_url


class TestPostLoginState:
    def test_post_wrapped_wameiji(self, tmp_path):
        xf = tmp_path / "xianyu_state.json"
        wf = tmp_path / "wameiji_state.json"
        server, thread, base = _start_server(tmp_path, xf, wf)
        try:
            content = json.dumps({
                "playwright_storage_state": {
                    "cookies": [{"name": "sid", "value": "x", "domain": ".meruki.cn", "path": "/", "expires": -1}],
                    "origins": [],
                },
            })
            code, body = _post_json(f"{base}/api/login-state/wameiji", {"content": content})
            assert code == 200
            assert body["saved"] is True
            assert body["platform"] == "wameiji"
            assert body["cookie_count"] == 1
            assert wf.exists()
            saved = json.loads(wf.read_text(encoding="utf-8"))
            assert "playwright_storage_state" in saved
        finally:
            server.shutdown(); server.server_close()

    def test_post_flat_shape_auto_wraps(self, tmp_path):
        xf = tmp_path / "xianyu_state.json"
        wf = tmp_path / "wameiji_state.json"
        server, thread, base = _start_server(tmp_path, xf, wf)
        try:
            content = json.dumps({
                "cookies": [{"name": "sid", "value": "x", "domain": ".meruki.cn", "path": "/", "expires": -1}],
                "origins": [],
            })
            code, body = _post_json(f"{base}/api/login-state/wameiji", {"content": content})
            assert code == 200
            saved = json.loads(wf.read_text(encoding="utf-8"))
            assert "playwright_storage_state" in saved
            assert isinstance(saved["playwright_storage_state"]["cookies"], list)
        finally:
            server.shutdown(); server.server_close()

    def test_post_xianyu(self, tmp_path):
        xf = tmp_path / "xianyu_state.json"
        wf = tmp_path / "wameiji_state.json"
        server, thread, base = _start_server(tmp_path, xf, wf)
        try:
            content = json.dumps({
                "playwright_storage_state": {
                    "cookies": [{"name": "sid", "value": "y", "domain": ".goofish.com", "path": "/", "expires": -1}],
                    "origins": [],
                },
            })
            code, body = _post_json(f"{base}/api/login-state/xianyu", {"content": content})
            assert code == 200
            assert body["platform"] == "xianyu"
            assert xf.exists()
        finally:
            server.shutdown(); server.server_close()

    def test_post_invalid_json_returns_400(self, tmp_path):
        xf = tmp_path / "xianyu_state.json"
        wf = tmp_path / "wameiji_state.json"
        server, thread, base = _start_server(tmp_path, xf, wf)
        try:
            code, body = _post_json(f"{base}/api/login-state/wameiji", {"content": "not json"})
            assert code == 400
            assert body["error"] == "invalid_json"
            assert "detail" in body
            assert not wf.exists()
        finally:
            server.shutdown(); server.server_close()

    def test_post_empty_content_returns_400(self, tmp_path):
        xf = tmp_path / "xianyu_state.json"
        wf = tmp_path / "wameiji_state.json"
        server, thread, base = _start_server(tmp_path, xf, wf)
        try:
            code, body = _post_json(f"{base}/api/login-state/wameiji", {"content": ""})
            assert code == 400
            assert body["error"] == "content_required"
        finally:
            server.shutdown(); server.server_close()

    def test_post_missing_cookies_returns_400(self, tmp_path):
        xf = tmp_path / "xianyu_state.json"
        wf = tmp_path / "wameiji_state.json"
        server, thread, base = _start_server(tmp_path, xf, wf)
        try:
            code, body = _post_json(f"{base}/api/login-state/wameiji", {"content": json.dumps({"foo": 1})})
            assert code == 400
            assert body["error"] == "invalid_state_file"
        finally:
            server.shutdown(); server.server_close()

    def test_post_overwrites_existing(self, tmp_path):
        xf = tmp_path / "xianyu_state.json"
        wf = tmp_path / "wameiji_state.json"
        wf.write_text("stale content", encoding="utf-8")
        server, thread, base = _start_server(tmp_path, xf, wf)
        try:
            content = json.dumps({"cookies": [{"name": "sid", "value": "x", "domain": ".meruki.cn"}], "origins": []})
            code, body = _post_json(f"{base}/api/login-state/wameiji", {"content": content})
            assert code == 200
            saved = json.loads(wf.read_text(encoding="utf-8"))
            assert "playwright_storage_state" in saved
        finally:
            server.shutdown(); server.server_close()


class TestDeleteLoginState:
    def test_delete_existing(self, tmp_path):
        xf = tmp_path / "xianyu_state.json"
        wf = tmp_path / "wameiji_state.json"
        wf.write_text("stale", encoding="utf-8")
        server, thread, base = _start_server(tmp_path, xf, wf)
        try:
            code, body = _delete(f"{base}/api/login-state/wameiji")
            assert code == 200
            assert body["deleted"] is True
            assert not wf.exists()
        finally:
            server.shutdown(); server.server_close()

    def test_delete_missing_is_idempotent(self, tmp_path):
        xf = tmp_path / "xianyu_state.json"
        wf = tmp_path / "wameiji_state.json"
        server, thread, base = _start_server(tmp_path, xf, wf)
        try:
            code, body = _delete(f"{base}/api/login-state/wameiji")
            assert code == 200
            assert body["deleted"] is False
            assert body.get("reason") == "missing"
        finally:
            server.shutdown(); server.server_close()


class TestInspectAfterPost:
    def test_posted_wameiji_inspects_ready(self, tmp_path):
        xf = tmp_path / "xianyu_state.json"
        wf = tmp_path / "wameiji_state.json"
        server, thread, base = _start_server(tmp_path, xf, wf)
        try:
            content = json.dumps({"cookies": [{"name": "sid", "value": "x", "domain": ".meruki.cn"}], "origins": []})
            _post_json(f"{base}/api/login-state/wameiji", {"content": content})
            body = _get(f"{base}/api/login-state/wameiji")
            assert body["status"] == "ready"
            assert ".meruki.cn" in body["cookie_domains"]
        finally:
            server.shutdown(); server.server_close()

    def test_posted_xianyu_inspects_ready(self, tmp_path):
        xf = tmp_path / "xianyu_state.json"
        wf = tmp_path / "wameiji_state.json"
        server, thread, base = _start_server(tmp_path, xf, wf)
        try:
            content = json.dumps({"cookies": [{"name": "sid", "value": "y", "domain": ".goofish.com"}], "origins": []})
            _post_json(f"{base}/api/login-state/xianyu", {"content": content})
            body = _get(f"{base}/api/login-state/xianyu")
            assert body["status"] == "ready"
            assert ".goofish.com" in body["cookie_domains"]
        finally:
            server.shutdown(); server.server_close()


class TestAtomicWrite:
    def test_post_does_not_leave_tmp_file(self, tmp_path):
        xf = tmp_path / "xianyu_state.json"
        wf = tmp_path / "wameiji_state.json"
        server, thread, base = _start_server(tmp_path, xf, wf)
        try:
            content = json.dumps({"cookies": [{"name": "sid", "value": "x", "domain": ".meruki.cn"}], "origins": []})
            _post_json(f"{base}/api/login-state/wameiji", {"content": content})
            assert not (tmp_path / "wameiji_state.json.tmp").exists()
        finally:
            server.shutdown(); server.server_close()