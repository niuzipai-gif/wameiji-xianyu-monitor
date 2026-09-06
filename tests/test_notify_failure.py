import sqlite3

import pytest

from cd_monitor.core.models import MarketItem, Opportunity
from cd_monitor.notify.base import Notifier, send_and_record
from cd_monitor.storage.sqlite import init_db


class FailingNotifier(Notifier):
    channel = "failing"

    def send(self, opportunity: Opportunity, dry_run: bool = True):
        raise RuntimeError("webhook failed")


def test_notification_failure_is_recorded_and_reraised(tmp_path) -> None:
    db_path = tmp_path / "notify-failure.db"
    init_db(db_path)
    opportunity = Opportunity(
        catalog_no="SRCL-3520",
        item=MarketItem(source="wameiji", title="SRCL-3520", price=1000, external_item_id="abc"),
        xianyu_reference_price=200,
        expected_sale_price=180,
        landed_cost=100,
        expected_revenue=170,
        expected_profit=70,
        net_margin=0.7,
        turnover_adjusted_roi=0.7,
        match_confidence=0.9,
        valid_xianyu_sample_count=3,
        liquidity_status="normal",
        decision="strong_alert",
        opportunity_hash="opp-hash",
    )

    with pytest.raises(RuntimeError, match="webhook failed"):
        send_and_record(db_path, 1, opportunity, FailingNotifier(), dry_run=False)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT channel, status, response_text FROM sent_alerts").fetchone()
    assert row[0] == "failing"
    assert row[1] == "failed"
    assert "webhook failed" in row[2]
