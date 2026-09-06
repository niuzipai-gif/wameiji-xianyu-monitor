import sqlite3

from cd_monitor.core.hashing import build_alert_hash
from cd_monitor.core.models import MarketItem, Opportunity, WatchItem
from cd_monitor.storage.sqlite import (
    add_watch,
    disable_watch,
    init_db,
    insert_opportunity,
    insert_sent_alert,
    list_candidate_rechecks,
    list_watch,
    schedule_candidate_recheck,
    update_candidate_recheck_status,
    update_watch,
)


def test_init_db_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "monitor.db"
    init_db(db_path)
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"watchlist", "search_runs", "market_items", "xianyu_price_samples", "opportunities", "sent_alerts"} <= tables


def test_alert_hash_deduplicates_but_price_change_changes_hash(tmp_path) -> None:
    db_path = tmp_path / "monitor.db"
    init_db(db_path)
    first = build_alert_hash("wameiji", "abc", 100, 200, 120, "strong_alert")
    second = build_alert_hash("wameiji", "abc", 90, 200, 120, "strong_alert")
    assert first != second
    assert insert_sent_alert(db_path, 1, first, "dry-run", "sent", "{}") is True
    assert insert_sent_alert(db_path, 1, first, "dry-run", "sent", "{}") is False
    assert insert_sent_alert(db_path, 1, second, "dry-run", "sent", "{}") is True


def test_update_and_disable_watch(tmp_path) -> None:
    db_path = tmp_path / "watch.db"
    watch_id = add_watch(db_path, WatchItem(catalog_no="SRCL-3520", priority=1))

    updated = update_watch(
        db_path,
        watch_id,
        {
            "artist": "L'Arc-en-Ciel",
            "priority": 3,
            "expected_holding_days": 45,
            "required_keywords": ["初回限定", "帯付き"],
        },
    )

    assert updated is True
    watches = list_watch(db_path)
    assert watches[0].artist == "L'Arc-en-Ciel"
    assert watches[0].priority == 3
    assert watches[0].expected_holding_days == 45
    assert watches[0].required_keywords == ["初回限定", "帯付き"]

    assert disable_watch(db_path, watch_id) is True
    assert list_watch(db_path) == []


def test_insert_opportunity_returns_existing_id_for_duplicate_hash(tmp_path) -> None:
    db_path = tmp_path / "opportunity-id.db"
    opportunity = _opportunity("SRCL-3520", "same-hash")

    first_id = insert_opportunity(db_path, opportunity)
    second_id = insert_opportunity(db_path, opportunity)

    assert second_id == first_id
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 1


def test_schedule_candidate_recheck_upserts_pending_plan(tmp_path) -> None:
    db_path = tmp_path / "recheck.db"
    first = schedule_candidate_recheck(
        db_path,
        opportunity_id=7,
        scheduled_at="2026-01-01T12:02:00",
        reason="first candidate pass",
    )
    second = schedule_candidate_recheck(
        db_path,
        opportunity_id=7,
        scheduled_at="2026-01-01T12:03:00",
        reason="updated candidate pass",
    )

    rows = list_candidate_rechecks(db_path)
    assert first["id"] == second["id"]
    assert len(rows) == 1
    assert rows[0]["opportunity_id"] == 7
    assert rows[0]["status"] == "pending"
    assert rows[0]["scheduled_at"] == "2026-01-01T12:03:00"
    assert rows[0]["reason"] == "updated candidate pass"


def test_update_candidate_recheck_status_moves_pending_to_history(tmp_path) -> None:
    db_path = tmp_path / "recheck-status.db"
    pending = schedule_candidate_recheck(
        db_path,
        opportunity_id=8,
        scheduled_at="2026-01-01T12:02:00",
        reason="candidate waiting",
    )

    updated = update_candidate_recheck_status(
        db_path,
        int(pending["id"]),
        "confirmed",
        "price and availability still match",
    )

    assert updated["status"] == "confirmed"
    assert updated["reason"] == "price and availability still match"
    assert list_candidate_rechecks(db_path) == []
    history = list_candidate_rechecks(db_path, status=None)
    assert len(history) == 1
    assert history[0]["status"] == "confirmed"


def test_candidate_recheck_allows_multiple_confirmed_history_rows(tmp_path) -> None:
    db_path = tmp_path / "recheck-history.db"
    first = schedule_candidate_recheck(
        db_path,
        opportunity_id=9,
        scheduled_at="2026-01-01T12:02:00",
        reason="first pass",
    )
    update_candidate_recheck_status(db_path, int(first["id"]), "confirmed", "first confirmed")
    second = schedule_candidate_recheck(
        db_path,
        opportunity_id=9,
        scheduled_at="2026-01-01T12:04:00",
        reason="second pass",
    )

    update_candidate_recheck_status(db_path, int(second["id"]), "confirmed", "second confirmed")

    history = list_candidate_rechecks(db_path, status=None, opportunity_id=9)
    assert [row["status"] for row in history] == ["confirmed", "confirmed"]
    assert [row["reason"] for row in history] == ["first confirmed", "second confirmed"]


def test_init_db_migrates_legacy_recheck_unique_constraint(tmp_path) -> None:
    db_path = tmp_path / "legacy-recheck.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE candidate_rechecks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              opportunity_id INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              reason TEXT,
              scheduled_at TEXT NOT NULL,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(opportunity_id, status)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO candidate_rechecks (opportunity_id, status, reason, scheduled_at)
            VALUES (10, 'confirmed', 'legacy confirmed', '2026-01-01T12:00:00')
            """
        )

    pending = schedule_candidate_recheck(
        db_path,
        opportunity_id=10,
        scheduled_at="2026-01-01T12:02:00",
        reason="new pending",
    )
    update_candidate_recheck_status(db_path, int(pending["id"]), "confirmed", "new confirmed")

    history = list_candidate_rechecks(db_path, status=None, opportunity_id=10)
    assert [row["reason"] for row in history] == ["legacy confirmed", "new confirmed"]


def _opportunity(catalog_no: str, opportunity_hash: str) -> Opportunity:
    return Opportunity(
        catalog_no=catalog_no,
        item=MarketItem(source="wameiji", title=f"{catalog_no} title", price=1000),
        xianyu_reference_price=200,
        expected_sale_price=180,
        landed_cost=100,
        expected_revenue=170,
        expected_profit=70,
        net_margin=0.7,
        turnover_adjusted_roi=0.7,
        match_confidence=0.95,
        valid_xianyu_sample_count=3,
        liquidity_status="normal",
        decision="strong_alert",
        opportunity_hash=opportunity_hash,
    )
