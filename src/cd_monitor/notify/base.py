from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict
import json
from typing import Any

from cd_monitor.core.hashing import build_alert_hash
from cd_monitor.core.models import Opportunity
from cd_monitor.storage.sqlite import insert_sent_alert


class Notifier(ABC):
    channel: str

    @abstractmethod
    def send(self, opportunity: Opportunity, dry_run: bool = True) -> dict[str, Any]:
        raise NotImplementedError


def build_message(opportunity: Opportunity) -> dict[str, Any]:
    return {
        "title": f"CD 价差候选 / {opportunity.decision}",
        "catalog_no": opportunity.catalog_no,
        "jan": opportunity.item.jan,
        "japan_title": opportunity.item.title,
        "source_site": opportunity.item.source_site,
        "wameiji_price": opportunity.item.price,
        "landed_cost": opportunity.landed_cost,
        "xianyu_reference_price": opportunity.xianyu_reference_price,
        "expected_sale_price": opportunity.expected_sale_price,
        "expected_profit": opportunity.expected_profit,
        "net_margin": opportunity.net_margin,
        "valid_xianyu_sample_count": opportunity.valid_xianyu_sample_count,
        "liquidity_status": opportunity.liquidity_status,
        "match_confidence": opportunity.match_confidence,
        "turnover_adjusted_roi": opportunity.turnover_adjusted_roi,
        "risk_labels": opportunity.risk_labels,
        "url": opportunity.item.url,
        "image_url": opportunity.item.image_url,
        "screenshot_path": opportunity.item.screenshot_path,
        "availability": opportunity.item.availability,
        "condition_text": opportunity.item.condition_text,
        "xianyu_search_keyword": opportunity.catalog_no,
        "manual_review": opportunity.review_advice,
        "raw": asdict(opportunity),
    }


def send_and_record(
    db_path: str,
    opportunity_id: int,
    opportunity: Opportunity,
    notifier: Notifier,
    dry_run: bool = True,
) -> dict[str, Any]:
    alert_hash = build_alert_hash(
        opportunity.item.source,
        opportunity.item.external_item_id,
        opportunity.item.price,
        opportunity.xianyu_reference_price,
        opportunity.landed_cost,
        opportunity.decision,
    )
    try:
        response = notifier.send(opportunity, dry_run=dry_run)
    except Exception as exc:
        insert_sent_alert(
            db_path,
            opportunity_id,
            alert_hash,
            notifier.channel,
            "failed",
            f"{type(exc).__name__}: {exc}",
        )
        raise
    recorded = insert_sent_alert(
        db_path,
        opportunity_id,
        alert_hash,
        notifier.channel,
        response.get("status", "sent"),
        json.dumps(response, ensure_ascii=False),
    )
    response["recorded"] = recorded
    response["alert_hash"] = alert_hash
    return response
