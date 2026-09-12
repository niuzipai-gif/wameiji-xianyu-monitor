from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from cd_monitor.pages_snapshot import (
    ALLOWED_IMAGE_HOSTS,
    DownloadedImage,
    SnapshotExportError,
    export_pages_snapshot,
)


def png_bytes(colour: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (160, 160), colour).save(output, format="PNG")
    return output.getvalue()


def verified_board() -> dict[str, object]:
    canonical_key = "catalog:nzs955|edition:initial_limited|condition:sealed"
    return {
        "schema_version": 2,
        "generated_at": "2026-09-09T00:20:00+08:00",
        "display_exchange_rate_cny_per_jpy": 0.0455,
        "strategy": {
            "policy_version": "wameiji-xianyu-net-v2",
            "trade_direction": "wameiji_jpy_to_xianyu_cny",
            "minimum_net_margin": 0.0,
            "margin_denominator": "xianyu_sale_price_cny",
        },
        "summary": {
            "evaluated_count": 3,
            "eligible_count": 1,
            "below_margin_count": 1,
            "cost_pending_count": 1,
            "waiting_wameiji_count": 0,
            "waiting_xianyu_count": 0,
        },
        "eligible": [
            {
                "comparison_id": 4,
                "canonical_product_key": canonical_key,
                "debug_cookie": "must-not-publish",
                "xianyu": {
                    "source": "xianyu",
                    "canonical_product_key": canonical_key,
                    "title": "Kiss Plan 初回限定盘B",
                    "price": 121,
                    "currency": "CNY",
                    "url": "https://www.goofish.com/item?id=1",
                    "image_url": "https://img.alicdn.com/example.png",
                    "evidence_level": "search_card",
                    "captured_at": "2026-09-08T23:28:24+08:00",
                    "condition_group": "sealed",
                    "session_cookie": "must-not-publish",
                },
                "wameiji": {
                    "source": "wameiji",
                    "canonical_product_key": canonical_key,
                    "title": "Kiss Plan 初回限定盤B",
                    "price": 2250,
                    "currency": "JPY",
                    "url": "https://meruki.cn/mall/paypay/detail/z1",
                    "image_url": "https://auctions.c.yimg.jp/example.png",
                    "evidence_level": "detail_verified",
                    "captured_at": "2026-09-08T23:28:19+08:00",
                    "condition_group": "sealed",
                },
                "calculation": {
                    "status": "eligible",
                    "sale_price_cny": 121,
                    "landed_cost_cny": 70,
                    "expected_profit_cny": 49.06,
                    "net_margin": 0.40545454545454546,
                    "created_at": "2026-09-09T00:21:00+08:00",
                    "cost_breakdown": {
                        "policy_version": "wameiji-xianyu-net-v2",
                        "minimum_net_margin": 0.0,
                        "missing_fields": [],
                        "xianyu_sale_cny": 121,
                        "xianyu_seller_fee_cny": 1.936,
                        "wameiji_exchange_rate_cny_per_jpy": 0.0455,
                        "wameiji_item_jpy": 850,
                        "wameiji_item_cny": 38.675,
                        "wameiji_domestic_shipping_jpy": 0,
                        "wameiji_domestic_shipping_cny": 0,
                        "wameiji_proxy_fee_jpy": 200,
                        "wameiji_proxy_fee_cny": 9.1,
                        "wameiji_purchase_cny": 47.775,
                        "international_shipping_cny": 15,
                        "china_postage_cny": 5,
                        "packaging_cny": 2,
                        "after_sale_reserve_cny": 0,
                        "risk_reserve_cny": 0,
                        "tax_cny": 0,
                        "landed_cost_cny": 69.775,
                        "net_profit_cny": 49.289,
                        "net_margin": 0.40734710743801655,
                    },
                },
            }
        ],
        "below_margin": [],
        "cost_pending": [],
        "waiting_wameiji": [],
        "waiting_xianyu": [],
        "collector": {"state": "paused"},
    }


def test_publish_image_allowlist_covers_verified_wameiji_marketplace_cdns() -> None:
    assert {
        "auctions.c.yimg.jp",
        "thumbnail.image.rakuten.co.jp",
        "assets.mercari-shops-static.com",
        "static.mercdn.net",
        "img.fril.jp",
    }.issubset(ALLOWED_IMAGE_HOSTS)
    assert "meruki.cn" not in ALLOWED_IMAGE_HOSTS


def test_export_rewrites_both_images_and_writes_hash_manifest(tmp_path: Path) -> None:
    images = {
        "https://img.alicdn.com/example.png": png_bytes("gold"),
        "https://auctions.c.yimg.jp/example.png": png_bytes("pink"),
    }

    def fetch(url: str) -> DownloadedImage:
        return DownloadedImage(body=images[url], content_type="image/png")

    result = export_pages_snapshot(
        verified_board(),
        tmp_path / "web",
        generated_at=datetime.fromisoformat("2026-09-09T00:30:00+08:00"),
        fetch_image=fetch,
    )

    snapshot_text = result.snapshot_path.read_text(encoding="utf-8")
    payload = json.loads(snapshot_text)
    card = payload["eligible"][0]
    asset_prefix = "assets/dual-market/20260909T003000+0800/"
    assert card["xianyu"]["image_url"].startswith(asset_prefix)
    assert card["wameiji"]["image_url"].startswith(asset_prefix)
    assert "img.alicdn.com" not in snapshot_text
    assert "auctions.c.yimg.jp" not in snapshot_text
    assert "must-not-publish" not in snapshot_text
    assert payload["mode"] == "verified_static_snapshot"
    assert payload["schema_version"] == 2
    assert payload["display_exchange_rate_cny_per_jpy"] == 0.0455
    assert payload["strategy"]["policy_version"] == "wameiji-xianyu-net-v2"
    assert payload["summary"] == verified_board()["summary"]
    assert payload["below_margin"] == []
    assert payload["cost_pending"] == []
    assert payload["summary"]["cost_pending_count"] == 1
    assert card["calculation"]["cost_breakdown"]["international_shipping_cny"] == 15

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["published_comparisons"] == 1
    assert len(manifest["assets"]) == 2
    for asset in manifest["assets"]:
        body = (tmp_path / "web" / asset["path"]).read_bytes()
        assert asset["sha256"] == hashlib.sha256(body).hexdigest()
        assert asset["width"] == 160
        assert asset["height"] == 160


def test_export_drops_a_card_when_either_source_image_fails(tmp_path: Path) -> None:
    board = verified_board()
    second = copy.deepcopy(board["eligible"][0])
    second["comparison_id"] = 5
    second["xianyu"]["image_url"] = "https://img.alicdn.com/second.png"
    second["wameiji"]["image_url"] = "https://auctions.c.yimg.jp/second.png"
    board["eligible"].append(second)
    board["summary"]["evaluated_count"] = 4
    board["summary"]["eligible_count"] = 2

    def fetch(url: str) -> DownloadedImage:
        if url.endswith("/second.png") and "yimg.jp" in url:
            raise SnapshotExportError("image download failed")
        return DownloadedImage(png_bytes("blue"), "image/png")

    result = export_pages_snapshot(
        board,
        tmp_path / "web",
        generated_at=datetime.fromisoformat("2026-09-09T00:30:00+08:00"),
        fetch_image=fetch,
    )

    payload = json.loads(result.snapshot_path.read_text(encoding="utf-8"))
    assert [item["comparison_id"] for item in payload["eligible"]] == [4]
    assert result.dropped_comparison_ids == (5,)
    assert not list(result.snapshot_path.parents[1].glob("assets/dual-market/*/5-*"))


def test_export_rejects_an_invalid_image_body(tmp_path: Path) -> None:
    def fetch(_url: str) -> DownloadedImage:
        return DownloadedImage(b"not an image" * 32, "image/png")

    with pytest.raises(SnapshotExportError, match="no publishable comparisons"):
        export_pages_snapshot(
            verified_board(),
            tmp_path / "web",
            generated_at=datetime.fromisoformat("2026-09-09T00:30:00+08:00"),
            fetch_image=fetch,
        )


def test_export_rejects_wrong_source_evidence_levels(tmp_path: Path) -> None:
    board = verified_board()
    board["eligible"][0]["xianyu"]["evidence_level"] = "detail_verified"

    with pytest.raises(SnapshotExportError, match="no publishable comparisons"):
        export_pages_snapshot(
            board,
            tmp_path / "web",
            generated_at=datetime.fromisoformat("2026-09-09T00:30:00+08:00"),
            fetch_image=lambda _url: DownloadedImage(png_bytes("green"), "image/png"),
        )


def test_all_failed_cards_preserve_the_previous_snapshot(tmp_path: Path) -> None:
    web_dir = tmp_path / "web"
    snapshot_path = web_dir / "data" / "dual-market-snapshot.json"
    manifest_path = web_dir / "data" / "dual-market-snapshot.manifest.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text('{"previous": true}', encoding="utf-8")
    manifest_path.write_text('{"previous_manifest": true}', encoding="utf-8")

    def fail(_url: str) -> DownloadedImage:
        raise SnapshotExportError("blocked")

    with pytest.raises(SnapshotExportError, match="no publishable comparisons"):
        export_pages_snapshot(
            verified_board(),
            web_dir,
            generated_at=datetime.fromisoformat("2026-09-09T00:30:00+08:00"),
            fetch_image=fail,
        )

    assert snapshot_path.read_text(encoding="utf-8") == '{"previous": true}'
    assert manifest_path.read_text(encoding="utf-8") == '{"previous_manifest": true}'


def test_zero_eligible_board_publishes_an_honest_zero_state_without_assets(
    tmp_path: Path,
) -> None:
    board = verified_board()
    board["eligible"] = []
    board["summary"]["eligible_count"] = 0

    result = export_pages_snapshot(
        board,
        tmp_path / "web",
        generated_at=datetime.fromisoformat("2026-09-09T00:30:00+08:00"),
        fetch_image=lambda _url: (_ for _ in ()).throw(
            AssertionError("zero-state attempted to download an image")
        ),
    )

    payload = json.loads(result.snapshot_path.read_text(encoding="utf-8"))
    assert payload["eligible"] == []
    assert payload["summary"]["evaluated_count"] == 3
    assert payload["summary"]["below_margin_count"] == 1
    assert payload["summary"]["cost_pending_count"] == 1
    assert result.asset_paths == ()


def test_export_rejects_a_card_without_positive_profit_even_when_v2_margin_is_zero(
    tmp_path: Path,
) -> None:
    board = verified_board()
    board["eligible"][0]["calculation"]["expected_profit_cny"] = 0

    with pytest.raises(SnapshotExportError, match="no publishable eligible comparisons"):
        export_pages_snapshot(
            board,
            tmp_path / "web",
            generated_at=datetime.fromisoformat("2026-09-09T00:30:00+08:00"),
            fetch_image=lambda _url: DownloadedImage(png_bytes("green"), "image/png"),
        )


def test_export_cli_accepts_a_local_board_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    board_file = tmp_path / "board.json"
    board_file.write_text(
        json.dumps(verified_board(), ensure_ascii=False),
        encoding="utf-8",
    )
    script_path = Path("scripts/export_dual_market_pages_snapshot.py")
    spec = importlib.util.spec_from_file_location("pages_snapshot_cli", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def fetch(_url: str) -> DownloadedImage:
        return DownloadedImage(png_bytes("purple"), "image/png")

    exit_code = module.main(
        [
            "--board-file",
            str(board_file),
            "--web-dir",
            str(tmp_path / "web"),
            "--generated-at",
            "2026-09-09T00:30:00+08:00",
        ],
        fetch_image=fetch,
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["published_comparisons"] == 1
    assert output["dropped_comparison_ids"] == []
