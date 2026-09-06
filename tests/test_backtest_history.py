from cd_monitor.core.models import MarketItem, XianyuPriceSample
from cd_monitor.review.backtest import backtest_database_history
from cd_monitor.storage.sqlite import init_db, insert_market_items, insert_xianyu_samples


def test_backtest_replays_database_market_items_with_title_only_catalog_no(tmp_path) -> None:
    db_path = tmp_path / "history.db"
    init_db(db_path)
    insert_market_items(
        db_path,
        [
            MarketItem(
                source="wameiji",
                source_site="mercari",
                external_item_id="w1",
                catalog_no=None,
                title="Artist SRCL-3520 初回限定",
                price=1200,
                availability="available",
            )
        ],
    )
    insert_xianyu_samples(
        db_path,
        [
            XianyuPriceSample(catalog_no="SRCL-3520", title="Artist SRCL-3520", price_cny=260),
            XianyuPriceSample(catalog_no="SRCL-3520", title="Artist SRCL-3520", price_cny=280),
            XianyuPriceSample(catalog_no="SRCL-3520", title="Artist SRCL-3520", price_cny=300),
        ],
    )

    opportunities = backtest_database_history(db_path, "SRCL-3520")

    assert len(opportunities) == 1
    assert opportunities[0].catalog_no == "SRCL-3520"
    assert opportunities[0].item.catalog_no is None
    assert opportunities[0].decision in {"strong_alert", "weak_alert", "review_only", "reject"}
