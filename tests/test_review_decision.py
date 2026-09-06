import sqlite3

from cd_monitor.storage.sqlite import init_db, insert_review_decision


def test_review_decision_is_recorded(tmp_path) -> None:
    db_path = tmp_path / "review.db"
    init_db(db_path)

    decision_id = insert_review_decision(
        db_path,
        opportunity_id=1,
        result="rejected_low_profit",
        note="margin too low after manual check",
    )

    assert decision_id == 1
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT opportunity_id, result, note FROM review_decisions").fetchone()
    assert row == (1, "rejected_low_profit", "margin too low after manual check")
