import sqlite3

from cd_monitor.core.models import MarketItem, Opportunity
from cd_monitor.notify.base import build_message, send_and_record
from cd_monitor.notify.feishu import FeishuNotifier
from cd_monitor.storage.sqlite import init_db


def test_dry_run_notification_records_unique_alert(tmp_path) -> None:
    db_path = tmp_path / "notify.db"
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

    first = send_and_record(db_path, 1, opportunity, FeishuNotifier(), dry_run=True)
    second = send_and_record(db_path, 1, opportunity, FeishuNotifier(), dry_run=True)

    assert first["recorded"] is True
    assert second["recorded"] is False
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sent_alerts").fetchone()[0] == 1


def test_notification_message_includes_review_context_fields() -> None:
    opportunity = Opportunity(
        catalog_no="SRCL-3520",
        item=MarketItem(
            source="wameiji",
            title="SRCL-3520",
            price=1000,
            source_site="mercari",
            url="https://example.invalid/item/1",
            image_url="https://example.invalid/item.jpg",
            availability="available",
            condition_text="盤傷なし",
        ),
        xianyu_reference_price=260,
        expected_sale_price=230,
        landed_cost=100,
        expected_revenue=215,
        expected_profit=115,
        net_margin=1.15,
        turnover_adjusted_roi=0.95,
        match_confidence=0.9,
        valid_xianyu_sample_count=4,
        liquidity_status="normal",
        decision="strong_alert",
        risk_labels=["clear"],
    )

    message = build_message(opportunity)

    assert message["image_url"] == "https://example.invalid/item.jpg"
    assert message["liquidity_status"] == "normal"
    assert message["turnover_adjusted_roi"] == 0.95
    assert message["availability"] == "available"
    assert message["condition_text"] == "盤傷なし"
    assert message["xianyu_search_keyword"] == "SRCL-3520"
