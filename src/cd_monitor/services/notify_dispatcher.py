"""Helper to fan out notifications across multiple channels for one or more opportunities."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from cd_monitor.config import ProjectConfig
from cd_monitor.core.models import Opportunity
from cd_monitor.notify.base import send_and_record
from cd_monitor.notify.dingtalk import DingTalkNotifier
from cd_monitor.notify.feishu import FeishuNotifier


# Decisions that warrant an automatic notification.
NOTIFY_DECISIONS = frozenset({"strong_alert", "weak_alert"})


def _resolve_channels(spec: str, config: ProjectConfig) -> list[str]:
    """Translate a channel spec string into a concrete list of channels to use.

    Returns only channels that are actually configured (webhook URL non-empty).
    "feishu" / "dingtalk" - single channel, only if its webhook is configured.
    "all" - feishu + dingtalk (order: feishu first), each only if configured.
    "" / None - empty list.
    """
    spec = (spec or "").strip().lower()
    if not spec or spec == "none":
        return []
    channels: list[str] = []
    if spec in {"all", "feishu"} and config.notify.feishu_webhook_url:
        channels.append("feishu")
    if spec in {"all", "dingtalk"} and config.notify.dingtalk_webhook_url:
        channels.append("dingtalk")
    if spec not in {"all", "feishu", "dingtalk"}:
        raise ValueError(f"Unknown notify channel spec: {spec!r}")
    return channels


def _notifier_for(channel: str, config: ProjectConfig, dry_run: bool):
    """Construct a Notifier for a given channel. In dry_run mode the webhook URL is irrelevant."""
    if channel == "feishu":
        # In dry_run, FeishuNotifier falls back to env var when webhook_url is empty.
        # We pass None to keep behavior identical to existing notify-dry-run path.
        return FeishuNotifier(None) if dry_run else FeishuNotifier(config.notify.feishu_webhook_url)
    if channel == "dingtalk":
        return DingTalkNotifier(None) if dry_run else DingTalkNotifier(config.notify.dingtalk_webhook_url)
    raise ValueError(f"Unknown channel: {channel!r}")


def notify_opportunities(
    db_path: str | Path,
    opportunity_ids: list[int],
    opportunities: list[Opportunity],
    *,
    channel_spec: str,
    config: ProjectConfig,
    dry_run: bool,
    include_review_only: bool = False,
) -> list[dict[str, Any]]:
    """Send notifications for each opportunity via each resolved channel.

    Returns a flat list of dicts, one per (opportunity_id, channel) pair, with:
      - opportunity_id
      - catalog_no
      - decision
      - channel
      - status (dry_run | sent | failed | no_channel_configured)
      - message (for dry_run)
      - response (for sent)
      - error (for failed)
      - alert_hash (for sent)

    Opportunities whose decision is not in NOTIFY_DECISIONS are skipped unless
    include_review_only=True (then review_only is also notified, but not reject).
    """
    channels = _resolve_channels(channel_spec, config)
    if not channels:
        return [
            {
                "opportunity_id": oid,
                "catalog_no": opp.catalog_no,
                "decision": opp.decision,
                "channel": channel_spec or "none",
                "status": "no_channel_configured",
                "error": (
                    f"No webhook configured for channel={channel_spec!r}. "
                    "Set notify.feishu_webhook_url / notify.dingtalk_webhook_url in config.yaml, "
                    "or use FEISHU_WEBHOOK_URL / DINGTALK_WEBHOOK_URL env vars."
                ),
            }
            for oid, opp in zip(opportunity_ids, opportunities)
        ]

    allowed_decisions = NOTIFY_DECISIONS | ({"review_only"} if include_review_only else set())
    results: list[dict[str, Any]] = []
    for oid, opp in zip(opportunity_ids, opportunities):
        if opp.decision not in allowed_decisions:
            results.append(
                {
                    "opportunity_id": oid,
                    "catalog_no": opp.catalog_no,
                    "decision": opp.decision,
                    "channel": None,
                    "status": "skipped",
                    "error": f"Decision {opp.decision!r} is not in notify set",
                }
            )
            continue
        for ch in channels:
            notifier = _notifier_for(ch, config, dry_run)
            try:
                resp = send_and_record(db_path, oid, opp, notifier, dry_run=dry_run)
                results.append(
                    {
                        "opportunity_id": oid,
                        "catalog_no": opp.catalog_no,
                        "decision": opp.decision,
                        "channel": ch,
                        "status": resp.get("status", "unknown"),
                        "message": resp.get("message"),
                        "response": resp.get("response"),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - record failure for caller
                results.append(
                    {
                        "opportunity_id": oid,
                        "catalog_no": opp.catalog_no,
                        "decision": opp.decision,
                        "channel": ch,
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    return results
