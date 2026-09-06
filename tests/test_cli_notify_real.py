import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _WebhookHandler(BaseHTTPRequestHandler):
    payloads: list[bytes] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.__class__.payloads.append(self.rfile.read(length))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return


def test_cli_notify_posts_to_configured_webhook(tmp_path) -> None:
    _WebhookHandler.payloads = []
    server = HTTPServer(("127.0.0.1", 0), _WebhookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    webhook_url = f"http://127.0.0.1:{server.server_port}/hook"
    db_path = tmp_path / "notify-real.db"
    snapshot_dir = tmp_path / "snapshots"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
app:
  db_path: "{db_path.as_posix()}"
  snapshot_dir: "{snapshot_dir.as_posix()}"
notify:
  feishu_webhook_url: "{webhook_url}"
""",
        encoding="utf-8",
    )
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "cd_monitor.cli",
                "--config",
                str(config_path),
                "scan-once",
                "--catalog-no",
                "SRCL-3520",
                "--source",
                "mock",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "cd_monitor.cli",
                "--config",
                str(config_path),
                "notify",
                "--opportunity-id",
                "1",
                "--channel",
                "feishu",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    payload = json.loads(result.stdout)
    assert payload["status"] == "sent"
    assert payload["recorded"] is True
    assert _WebhookHandler.payloads
