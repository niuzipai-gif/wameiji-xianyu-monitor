"""Offline regressions for persisted representative Xianyu samples."""

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from cd_monitor.core.models import CostConfig, EvaluationConfig
from cd_monitor.storage.sqlite import init_db


@pytest.fixture
def real_matcher(monkeypatch):
    """Use real matching/OCR text scores; disable optional image/OCR runtimes."""
    data_dir = Path(__file__).resolve().parents[1] / "data"
    monkeypatch.syspath_prepend(str(data_dir))

    def forbid_image_io(*args, **kwargs):
        raise AssertionError("Offline matching must use stored text and hashes")

    phash = ModuleType("phash_compute")
    phash.fetch_bytes = forbid_image_io
    monkeypatch.setitem(sys.modules, "phash_compute", phash)
    tesseract = ModuleType("pytesseract")
    tesseract.image_to_string = forbid_image_io
    monkeypatch.setitem(sys.modules, "pytesseract", tesseract)
    pil = ModuleType("PIL")
    pil.Image = SimpleNamespace(open=forbid_image_io)
    pil.ImageFile = SimpleNamespace(LOAD_TRUNCATED_IMAGES=False)
    monkeypatch.setitem(sys.modules, "PIL", pil)

    # Load the real text-tokenization module without optional binary dependencies.
    ocr_spec = importlib.util.spec_from_file_location("ocr_cover", data_dir / "ocr_cover.py")
    ocr_module = importlib.util.module_from_spec(ocr_spec)
    monkeypatch.setitem(sys.modules, "ocr_cover", ocr_module)
    ocr_spec.loader.exec_module(ocr_module)
    spec = importlib.util.spec_from_file_location("real_match_display", data_dir / "match_real_v2.py")
    matcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(matcher)
    monkeypatch.setattr(
        matcher, "load_cost_eval_config", lambda db_path: (CostConfig(), EvaluationConfig())
    )
    return matcher


@pytest.fixture
def market_db(tmp_path):
    db_path = tmp_path / "display.db"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO watchlist (catalog_no, artist, title_jp, title_cn, match_mode) "
            "VALUES ('TEST-1234', 'Artist', 'Moonlight', '月光', 'any')"
        )
        conn.execute(
            "INSERT INTO market_items "
            "(id, source, source_site, external_item_id, catalog_no, title, price, currency, "
            "availability, cover_text, image_phash) "
            "VALUES (1, 'wameiji', 'yahoo_auctions', 'jp-1', 'TEST-1234', "
            "'Artist Moonlight TEST-1234 CD', 1000, 'JPY', 'available', '', '')"
        )
    return db_path


def add_xianyu(db_path, *, title, cover_text="", phash="", sample_id=2, price=500):
    with sqlite3.connect(db_path) as conn:
        # The evaluator now requires three clean samples before producing a
        # reference price. Keep the matching fixture representative while
        # retaining id ``sample_id`` as the expected display choice.
        for offset in range(3):
            row_id = sample_id + offset
            conn.execute(
                "INSERT INTO market_items "
                "(id, source, external_item_id, catalog_no, title, price, currency, "
                "availability, cover_text, image_phash, url) "
                "VALUES (?, 'xianyu', ?, 'TEST-1234', ?, ?, 'CNY', 'available', ?, ?, ?)",
                (row_id, f"cn-{row_id}", title, price, cover_text, phash,
                 f"https://example.invalid/item/{row_id}"),
            )


def run_display_match(matcher, db_path):
    result = matcher.match_real_market_items(str(db_path))
    assert result["items_evaluated"] == 1
    assert result["opportunities_inserted"] == 1
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT xianyu_display_sample_id FROM opportunities"
        ).fetchall()
    assert len(rows) == 1
    return rows[0][0]


def test_ocr_match_without_phash_persists_sample_id(real_matcher, market_db):
    cover = "Moonlight Artist Album"
    with sqlite3.connect(market_db) as conn:
        conn.execute("UPDATE market_items SET cover_text=? WHERE id=1", (cover,))
    # Neither artist nor album appears in this sample's title; OCR is the evidence.
    add_xianyu(market_db, title="进口正版 CD", cover_text=cover)
    assert run_display_match(real_matcher, market_db) == 2


def test_artist_and_album_match_uses_stored_watch_titles(real_matcher, market_db):
    add_xianyu(market_db, title="A r t i s t Moonlight CD")
    assert run_display_match(real_matcher, market_db) == 2
    watch = real_matcher.make_watch_item(real_matcher.fetch_watches(str(market_db))[0])
    assert watch.title_jp == "Moonlight"
    assert watch.title_cn == "月光"


@pytest.mark.parametrize("title", ["Artist Another Album CD", "Other Moonlight CD"])
def test_same_catalog_and_partial_title_do_not_create_display_match(
    real_matcher, market_db, title
):
    add_xianyu(market_db, title=title)
    assert run_display_match(real_matcher, market_db) is None


@pytest.mark.parametrize("distance, expected_id", [(14, 2), (15, None)])
def test_any_mode_phash_threshold_is_preserved(real_matcher, market_db, distance, expected_id):
    with sqlite3.connect(market_db) as conn:
        conn.execute("UPDATE market_items SET image_phash='0' WHERE id=1")
    add_xianyu(market_db, title="进口正版 CD", phash=f"{(1 << distance) - 1:x}")
    assert run_display_match(real_matcher, market_db) == expected_id


@pytest.mark.parametrize("distance, expected_id", [(18, 2), (19, None)])
def test_all_mode_still_requires_artist_album_and_close_cover(
    real_matcher, market_db, distance, expected_id
):
    with sqlite3.connect(market_db) as conn:
        conn.execute("UPDATE watchlist SET match_mode='all'")
        conn.execute("UPDATE market_items SET image_phash='0' WHERE id=1")
    add_xianyu(market_db, title="Artist Moonlight CD", phash=f"{(1 << distance) - 1:x}")
    assert run_display_match(real_matcher, market_db) == expected_id
