from cd_monitor.core.models import EvaluationConfig
from cd_monitor.review.backtest import backtest_snapshots
from cd_monitor.storage.snapshots import save_snapshot


def test_backtest_replays_saved_mock_snapshot(tmp_path) -> None:
    snapshot = save_snapshot(
        tmp_path,
        "mock-scan",
        {
            "catalog_no": "SRCL-3520",
            "wameiji_items": [
                {
                    "source": "wameiji",
                    "source_site": "mercari",
                    "external_item_id": "w1",
                    "title": "Artist SRCL-3520 初回限定",
                    "price": 1200,
                    "currency": "JPY",
                    "availability": "available",
                }
            ],
            "xianyu_samples": [
                {"catalog_no": "SRCL-3520", "title": "Artist SRCL-3520", "price_cny": 260},
                {"catalog_no": "SRCL-3520", "title": "Artist SRCL-3520", "price_cny": 280},
                {"catalog_no": "SRCL-3520", "title": "Artist SRCL-3520", "price_cny": 300},
            ],
        },
    )

    opportunities = backtest_snapshots(tmp_path)

    assert snapshot.exists()
    assert len(opportunities) == 1
    assert opportunities[0].catalog_no == "SRCL-3520"
    assert opportunities[0].decision in {"strong_alert", "weak_alert", "review_only", "reject"}


def test_backtest_snapshots_uses_evaluation_config(tmp_path) -> None:
    save_snapshot(
        tmp_path,
        "mock-scan",
        {
            "catalog_no": "SRCL-3520",
            "wameiji_items": [
                {
                    "source": "wameiji",
                    "title": "Artist SRCL-3520 初回限定",
                    "price": 1200,
                    "currency": "JPY",
                    "availability": "available",
                }
            ],
            "xianyu_samples": [
                {"catalog_no": "SRCL-3520", "title": "Artist SRCL-3520", "price_cny": 260},
                {"catalog_no": "SRCL-3520", "title": "Artist SRCL-3520", "price_cny": 280},
                {"catalog_no": "SRCL-3520", "title": "Artist SRCL-3520", "price_cny": 300},
            ],
        },
    )

    opportunities = backtest_snapshots(
        tmp_path,
        evaluation_config=EvaluationConfig(strong_profit_min_cny=999, weak_profit_min_cny=999),
    )

    assert opportunities[0].decision == "review_only"
