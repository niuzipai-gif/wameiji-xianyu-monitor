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
        "summary": {
            "ready_count": 0,
            "negative_profit_count": 0,
            "cost_pending_count": 1,
        },
        "ready": [],
        "negative_profit": [],
        "cost_pending": [
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
                "calculation": {"status": "cost_pending", "sale_price_cny": 121},
            }
        ],
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
    card = payload["cost_pending"][0]
    asset_prefix = "assets/dual-market/20260909T003000+0800/"
    assert card["xianyu"]["image_url"].startswith(asset_prefix)
    assert card["wameiji"]["image_url"].startswith(asset_prefix)
    assert "img.alicdn.com" not in snapshot_text
    assert "auctions.c.yimg.jp" not in snapshot_text
    assert "must-not-publish" not in snapshot_text
    assert payload["mode"] == "verified_static_snapshot"
    assert payload["summary"]["cost_pending_count"] == 1

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
    second = copy.deepcopy(board["cost_pending"][0])
    second["comparison_id"] = 5
    second["xianyu"]["image_url"] = "https://img.alicdn.com/second.png"
    second["wameiji"]["image_url"] = "https://auctions.c.yimg.jp/second.png"
    board["cost_pending"].append(second)

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
    assert [item["comparison_id"] for item in payload["cost_pending"]] == [4]
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
    board["cost_pending"][0]["xianyu"]["evidence_level"] = "detail_verified"

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
