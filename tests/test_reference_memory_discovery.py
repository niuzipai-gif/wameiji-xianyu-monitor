from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from cd_monitor.core.models import MarketItem, XianyuPriceSample
from cd_monitor.services.discovery import scan_discovery_keyword
from cd_monitor.services.reference_memory import read_candidate_reference_match
from cd_monitor.storage.sqlite import (
    get_discovery_candidate_by_identity,
    init_db,
    list_discovery_detail_queue,
    list_discovery_pools,
    update_discovery_pool,
)


def _seed_reference_product(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO reference_products (stable_key, barcode, tokens_json, sample_count)
            VALUES (?, ?, ?, 1)
            """,
            (
                "jan:4547366558180",
                "4547366558180",
                json.dumps(["milet", "walkin", "lane"]),
            ),
        )


def test_discovery_persists_exact_reference_evidence_after_candidate_upsert(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "monitor.db"
    init_db(db_path)
    _seed_reference_product(db_path)
    pool = list_discovery_pools(db_path)[0]
    assert pool.id is not None
    update_discovery_pool(db_path, pool.id, {"detail_budget": 0, "xianyu_query_budget": 0})

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="milet Walkin In My Lane CD",
                jan="4547366558180",
                price=1500,
                currency="JPY",
                external_item_id="reference-exact",
                url="https://meruki.example/item/reference-exact",
                availability="available",
            )
        ]

    async def fetch_wameiji_detail(_item: MarketItem) -> MarketItem:
        raise AssertionError("detail reads are intentionally disabled")

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        raise AssertionError("resale reads are intentionally disabled")

    result = asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool.id,
            keyword="milet",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    assert result.candidate_count == 1
    candidate = get_discovery_candidate_by_identity(db_path, pool.id, "source:reference-exact")
    assert candidate is not None and candidate.id is not None
    assert read_candidate_reference_match(db_path, candidate.id) == {
        "candidate_id": candidate.id,
        "reference_product_id": 1,
        "score": 1.0,
        "match_kind": "exact_barcode",
        "evidence": {"matched_barcode": "4547366558180"},
    }


def test_reference_no_match_does_not_skip_the_existing_detail_queue(tmp_path: Path) -> None:
    db_path = tmp_path / "monitor.db"
    init_db(db_path)
    _seed_reference_product(db_path)
    pool = list_discovery_pools(db_path)[0]
    assert pool.id is not None
    update_discovery_pool(db_path, pool.id, {"detail_budget": 0, "xianyu_query_budget": 0})

    async def fetch_wameiji(_keyword: str) -> list[MarketItem]:
        return [
            MarketItem(
                source="wameiji",
                title="Different Artist Limited CD",
                price=1500,
                currency="JPY",
                external_item_id="reference-no-match",
                url="https://meruki.example/item/reference-no-match",
                availability="available",
            )
        ]

    async def fetch_wameiji_detail(_item: MarketItem) -> MarketItem:
        raise AssertionError("detail reads are intentionally disabled")

    async def fetch_xianyu(_query: str) -> list[XianyuPriceSample]:
        raise AssertionError("resale reads are intentionally disabled")

    asyncio.run(
        scan_discovery_keyword(
            db_path=db_path,
            pool_id=pool.id,
            keyword="different artist",
            fetch_wameiji=fetch_wameiji,
            fetch_wameiji_detail=fetch_wameiji_detail,
            fetch_xianyu=fetch_xianyu,
        )
    )

    candidate = get_discovery_candidate_by_identity(db_path, pool.id, "source:reference-no-match")
    assert candidate is not None and candidate.id is not None
    assert read_candidate_reference_match(db_path, candidate.id) == {
        "candidate_id": candidate.id,
        "reference_product_id": None,
        "score": 0.0,
        "match_kind": "no_match",
        "evidence": {"shared_tokens": []},
    }
    queued = list_discovery_detail_queue(db_path, pool.id, limit=10)
    assert [item.id for item in queued] == [candidate.id]
