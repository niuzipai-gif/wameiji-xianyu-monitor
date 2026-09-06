"""Bark push notification client (iOS / Bark app).

Bark is a free iOS push notification service: a tiny client + a server.
The notification URL shape is::

    https://api.day.app/<device_key>/<title>/<body>?group=<group>&icon=<icon>

The ``device_key`` is the per-device token the user gets when installing
the Bark app on their iPhone. We store the full Bark URL (with the device
key) in user_settings and POST a simple JSON payload to it.

Reference: https://github.com/Finb/Bark
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any
from urllib.request import urlopen as _urlopen

from cd_monitor.core.models import Opportunity
from cd_monitor.notify.base import Notifier, build_message


class BarkClient(Notifier):
    """Sends a push via the Bark iOS app gateway.

    The ``send`` method matches the Notifier contract (takes an
    Opportunity + dry_run). For the /test endpoint, use ``send_text``
    which takes raw title/body strings.
    """

    channel = "bark"

    def __init__(
        self,
        bark_url: str | None = None,
        *,
        group: str = "Kuro",
        timeout: float = 8.0,
    ) -> None:
        self.bark_url = (bark_url or os.getenv("BARK_URL", "") or "").rstrip("/")
        self.group = group or "Kuro"
        self.timeout = float(timeout)

    def send(self, opportunity: Opportunity, dry_run: bool = True) -> dict[str, Any]:
        message = build_message(opportunity)
        title = f"CD ???? / {opportunity.decision}"
        body = json.dumps(message, ensure_ascii=False, indent=None)
        return self.send_text(title, body, dry_run=dry_run)

    def send_text(
        self,
        title: str,
        body: str,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Send a raw title/body push. Used by /api/settings/notifications/test."""
        if dry_run:
            return {
                "channel": self.channel,
                "status": "dry_run",
                "title": title,
                "body": body,
            }
        if not self.bark_url:
            raise ValueError("BARK_URL is not configured")
        payload = json.dumps(
            {"title": title or "", "body": body or "", "group": self.group}
        ).encode("utf-8")
        request = urllib.request.Request(
            self.bark_url + "/",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with _urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
            return {
                "channel": self.channel,
                "status": "sent",
                "status_code": response.getcode(),
                "response": raw,
            }
        except urllib.error.HTTPError as exc:
            return {
                "channel": self.channel,
                "status": "failed",
                "status_code": exc.code,
                "error": f"http_{exc.code}",
            }
        except urllib.error.URLError as exc:
            return {
                "channel": self.channel,
                "status": "failed",
                "error": f"url_error:{exc.reason}",
            }


__all__ = ["BarkClient"]
