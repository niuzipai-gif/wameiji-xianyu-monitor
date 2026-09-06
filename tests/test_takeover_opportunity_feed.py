"""Offline regressions for databases created from the handoff source tree."""

import sqlite3
from pathlib import Path

import pytest

from cd_monitor.storage.migrations import SCHEMA_SQL
from cd_monitor.storage.sqlite import init_db
from cd_monitor.web_server import _opportunities_paged


CATALOG = "TAKEOVER-001"
XIANYU_FIELDS = (
    "xianyu_source", "xianyu_source_site", "xianyu_item_title",
    "xianyu_price_cny", "xianyu_url", "xianyu_image_url", "xianyu_availability",
)


def _market_item(conn, source, catalog=CATALOG, title="Album CD"):
    cursor = conn.execute(
        """INSERT INTO market_items
           (source, source_site, catalog_no, title, price, currency,
            url, image_url, availability)
           VALUES (?, ?, ?, ?, 120, ?, ?, ?, 'in_stock')""",
        (
            source, f"{source}-site", catalog, title,
            "CNY" if source == "xianyu" else "JPY",
            f"https://example.invalid/{source}/{catalog}/item",
            f"https://example.invalid/{source}/{catalog}/cover.jpg",
        ),
    )
    return cursor.lastrowid


def _opportunity(conn, purchase_id):
    cursor = conn.execute(
        """INSERT INTO opportunities
           (catalog_no, wameiji_item_id, expected_profit, match_confidence,
            risk_labels, opportunity_hash)
           VALUES (?, ?, 30, 0.9, '[]', 'takeover-opportunity')""",
        (CATALOG, purchase_id),
    )
    return cursor.lastrowid


def test_fresh_database_can_list_opportunities_without_external_data(tmp_path: Path):
    db = tmp_path / "fresh.db"
    init_db(db)

    result = _opportunities_paged(db, limit=10, offset=0)

    assert result == {"items": [], "total": 0, "limit": 10, "offset": 0, "has_more": False}
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO watchlist (catalog_no) VALUES (?)", (CATALOG,))
        assert conn.execute("SELECT match_mode FROM watchlist").fetchone() == ("any",)


def test_legacy_database_upgrade_is_idempotent_and_preserves_records(tmp_path: Path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA_SQL)
        purchase_id = _market_item(conn, "wameiji")
        opportunity_id = _opportunity(conn, purchase_id)
        conn.execute("INSERT INTO watchlist (catalog_no) VALUES (?)", (CATALOG,))

    init_db(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT id, catalog_no, wameiji_item_id, expected_profit, xianyu_display_sample_id "
            "FROM opportunities"
        ).fetchone() == (opportunity_id, CATALOG, purchase_id, 30, None)
        assert conn.execute("SELECT match_mode FROM watchlist").fetchone() == ("any",)
        sample_id = _market_item(conn, "xianyu")
        conn.execute(
            "UPDATE opportunities SET xianyu_display_sample_id = ? WHERE id = ?",
            (sample_id, opportunity_id),
        )
        conn.execute("UPDATE watchlist SET match_mode = 'all'")

    init_db(db)
    init_db(db)
    with sqlite3.connect(db) as conn:
        columns = conn.execute("PRAGMA table_info(opportunities)").fetchall()
        assert sum(column[1] == "xianyu_display_sample_id" for column in columns) == 1
        assert conn.execute(
            "SELECT id, catalog_no, wameiji_item_id, expected_profit, xianyu_display_sample_id "
            "FROM opportunities"
        ).fetchone() == (opportunity_id, CATALOG, purchase_id, 30, sample_id)
        assert conn.execute("SELECT match_mode FROM watchlist").fetchone() == ("all",)
        assert conn.execute("SELECT COUNT(*) FROM market_items").fetchone() == (2,)
    assert _opportunities_paged(db, limit=10, offset=0)["total"] == 1


@pytest.mark.parametrize("reference", ["other_catalog", "other_source", "null", "missing"])
def test_invalid_display_reference_never_leaks_xianyu_fields(tmp_path: Path, reference):
    db = tmp_path / "invalid-reference.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        purchase_id = _market_item(conn, "wameiji")
        opportunity_id = _opportunity(conn, purchase_id)
        # A valid same-catalog listing must not become an implicit fallback.
        _market_item(conn, "xianyu")
        if reference == "other_catalog":
            sample_id = _market_item(conn, "xianyu", catalog="UNRELATED-002")
        elif reference == "other_source":
            sample_id = purchase_id
        else:
            sample_id = None if reference == "null" else 99999
        conn.execute(
            "UPDATE opportunities SET xianyu_display_sample_id = ? WHERE id = ?",
            (sample_id, opportunity_id),
        )

    result = _opportunities_paged(db, limit=10, offset=0)

    assert result["total"] == len(result["items"]) == 1
    item = result["items"][0]
    assert {field: item[field] for field in XIANYU_FIELDS} == dict.fromkeys(XIANYU_FIELDS)
    assert item["item_title"] == "Album CD"
    assert item["image_url"] == f"https://example.invalid/wameiji/{CATALOG}/cover.jpg"


def test_wrong_source_merch_reference_does_not_hide_opportunity_or_change_count(tmp_path: Path):
    db = tmp_path / "wrong-source-merch.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        purchase_id = _market_item(conn, "wameiji")
        opportunity_id = _opportunity(conn, purchase_id)
        sample_id = _market_item(conn, "mercari_jp", title="アクリルキーホルダー")
        conn.execute(
            "UPDATE opportunities SET xianyu_display_sample_id = ? WHERE id = ?",
            (sample_id, opportunity_id),
        )

    result = _opportunities_paged(db, limit=10, offset=0)

    assert result["total"] == len(result["items"]) == 1
    assert result["items"][0]["xianyu_item_title"] is None


def test_explicit_same_catalog_xianyu_reference_displays_matching_fields(tmp_path: Path):
    db = tmp_path / "valid-reference.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        purchase_id = _market_item(conn, "wameiji")
        opportunity_id = _opportunity(conn, purchase_id)
        sample_id = _market_item(conn, "xianyu", title="Confirmed album CD")
        conn.execute(
            "UPDATE opportunities SET xianyu_display_sample_id = ? WHERE id = ?",
            (sample_id, opportunity_id),
        )

    result = _opportunities_paged(db, limit=10, offset=0)

    assert result["total"] == len(result["items"]) == 1
    item = result["items"][0]
    assert {field: item[field] for field in XIANYU_FIELDS} == {
        "xianyu_source": "xianyu",
        "xianyu_source_site": "xianyu-site",
        "xianyu_item_title": "Confirmed album CD",
        "xianyu_price_cny": 120,
        "xianyu_url": f"https://example.invalid/xianyu/{CATALOG}/item",
        "xianyu_image_url": f"https://example.invalid/xianyu/{CATALOG}/cover.jpg",
        "xianyu_availability": "in_stock",
    }


def test_feed_canonicalizes_relative_mercari_link(tmp_path: Path):
    db = tmp_path / "mercari-link.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        purchase_id = _market_item(conn, "wameiji")
        _opportunity(conn, purchase_id)
        conn.execute(
            "UPDATE market_items SET source_site = 'mercari_jp', url = '/item/m123' "
            "WHERE id = ?",
            (purchase_id,),
        )

    result = _opportunities_paged(db, limit=10, offset=0)

    assert result["total"] == len(result["items"]) == 1
    assert result["items"][0]["url"] == "https://jp.mercari.com/item/m123"
