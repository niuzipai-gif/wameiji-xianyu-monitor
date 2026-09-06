from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from cd_monitor.core.models import Opportunity
from cd_monitor.notify.base import Notifier, build_message


class DingTalkNotifier(Notifier):
    channel = "dingtalk"

    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = webhook_url or os.getenv("DINGTALK_WEBHOOK_URL", "")

    def send(self, opportunity: Opportunity, dry_run: bool = True) -> dict[str, Any]:
        message = build_message(opportunity)
        if dry_run:
            return {"channel": self.channel, "status": "dry_run", "message": message}
        if not self.webhook_url:
            raise ValueError("DINGTALK_WEBHOOK_URL is not configured")
        payload = json.dumps({"msgtype": "text", "text": {"content": json.dumps(message, ensure_ascii=False)}}).encode()
        request = urllib.request.Request(self.webhook_url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8")
        return {"channel": self.channel, "status": "sent", "response": body}
