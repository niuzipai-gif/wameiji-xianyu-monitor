from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import requests

from cd_monitor.core.dual_market import ListingObservation
from cd_monitor.storage.sqlite import (
    insert_listing_observation,
    list_price_comparisons,
)


def _load_script_module():
    script_path = Path("scripts/reprice_dual_market_board.py")
    spec = importlib.util.spec_from_file_location("reprice_dual_market_board_cli", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _observation(key: str, **changes: object) -> ListingObservation:
    observation = ListingObservation(
        source="wameiji",
        source_listing_id=f"w-{key}",
        canonical_product_key=f"catalog:{key}",
        title=f"Album {key}",
        price=1000,
        currency="JPY",
        url=f"https://meruki.cn/mall/mercari/detail/w-{key}",
        image_url=f"https://static.mercdn.net/item/detail/orig/photos/w-{key}.jpg",
        availability="available",
        condition_group="complete_used",
        completeness="complete",
        evidence_level="detail_verified",
        captured_at="2026-09-09T08:00:00+08:00",
    )
    return replace(observation, **changes)


def _write_detail(path: Path, *, shipping: str) -> None:
    path.write_text(
        f"""
        <header><p>挖煤姬汇率：1 日元 ≈ 0.0455 人民币</p></header>
        <main class="goods-detail">
          <div>日本国内运费 {shipping}</div>
          <div>代购手续费 200日元</div>
        </main>
        """,
        encoding="utf-8",
    )


def _insert_pair(
    db_path: Path,
    snapshot: Path,
    *,
    key: str,
    sale_price: float,
    shipping_fee: float | None,
) -> None:
    insert_listing_observation(
        db_path,
        _observation(
            key,
            raw_snapshot_path=str(snapshot),
            source_detail_fee=shipping_fee,
        ),
    )
    insert_listing_observation(
        db_path,
        _observation(
            key,
            source="xianyu",
            source_listing_id=f"x-{key}",
            price=sale_price,
            currency="CNY",
            url=f"https://www.goofish.com/item?id=x-{key}",
            image_url=f"https://img.alicdn.com/x-{key}.webp",
            evidence_level="search_card",
            raw_snapshot_path=f"saved/x-{key}.html",
        ),
    )


def test_reprices_saved_pairs_without_network_or_collection(tmp_path: Path, monkeypatch) -> None:
    module = _load_script_module()
    db_path = tmp_path / "monitor.db"
    eligible_detail = tmp_path / "eligible.html"
    below_detail = tmp_path / "below.html"
    pending_detail = tmp_path / "pending.html"
    _write_detail(eligible_detail, shipping="卖家承担")
    _write_detail(below_detail, shipping="卖家承担")
    _write_detail(pending_detail, shipping="买家承担")
    _insert_pair(
        db_path,
        eligible_detail,
        key="eligible",
        sale_price=200,
        shipping_fee=0,
    )
    _insert_pair(
        db_path,
        below_detail,
        key="below",
        sale_price=80,
        shipping_fee=0,
    )
    _insert_pair(
        db_path,
        pending_detail,
        key="pending",
        sale_price=200,
        shipping_fee=None,
    )

    def forbid_network(*_args, **_kwargs):
        raise AssertionError("offline repricing attempted a network request")

    monkeypatch.setattr(requests, "get", forbid_network)

    result = module.reprice_saved_pairs(db_path)

    assert result["policy_version"] == "wameiji-xianyu-net-v1"
    assert result["evaluated_count"] == 3
    assert result["eligible_count"] == 1
    assert result["below_margin_count"] == 1
    assert result["cost_pending_count"] == 1
    assert {row.status for row in list_price_comparisons(db_path, limit=10)} == {
        "eligible",
        "below_margin",
        "cost_pending",
    }
    pending = next(item for item in result["items"] if item["status"] == "cost_pending")
    assert pending["missing_fields"] == ["japan_domestic_shipping_jpy"]


def test_main_writes_audit_and_board_json_files(tmp_path: Path) -> None:
    module = _load_script_module()
    db_path = tmp_path / "monitor.db"
    detail = tmp_path / "eligible.html"
    _write_detail(detail, shipping="卖家承担")
    _insert_pair(db_path, detail, key="eligible", sale_price=200, shipping_fee=0)
    audit_path = tmp_path / "audit.json"
    board_path = tmp_path / "board.json"

    exit_code = module.main(
        [
            "--db",
            str(db_path),
            "--json-output",
            str(audit_path),
            "--board-output",
            str(board_path),
        ]
    )

    assert exit_code == 0
    assert audit_path.is_file()
    assert board_path.is_file()
    assert '"eligible_count": 1' in audit_path.read_text(encoding="utf-8")
    assert '"eligible"' in board_path.read_text(encoding="utf-8")
