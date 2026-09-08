from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image


def test_pages_build_includes_the_automatic_selection_board(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    destination = tmp_path / "site"

    subprocess.run(
        [
            sys.executable,
            "scripts/build_pages.py",
            "--api-base",
            "https://collector.example",
            "--dest",
            str(destination),
        ],
        cwd=root,
        check=True,
    )

    index = (destination / "index.html").read_text(encoding="utf-8")
    script = (destination / "discovery-ui.js").read_text(encoding="utf-8")
    assert "discovery-ui.js" in index
    assert "AI 闲鱼猎手" in index
    assert "首页机会流" in index
    assert 'id="sideScanDiscoveryBtn"' in index
    assert "浏览器扩展" not in index
    assert "/api/discovery/board" in script
    assert "/api/discovery/commands" in script
    assert "闲鱼 · 销售侧" in script
    assert "挖煤姬 · 进货侧" in script
    assert 'class="op-card discovery-op-card"' in script
    assert 'sideMarkup("xianyu"' in script
    assert 'sideMarkup("market"' in script
    assert "comparison-rail" in script
    assert "capture_state" in script
    assert "已自动暂停" in script
    assert "fresh_source_details" in script
    assert "resale_ready_candidates" in script
    assert "xianyu_login_state" in script
    assert "闲鱼需要扫码登录" in script
    assert "等待闲鱼价格样本" in script
    assert "每轮详情页上限" in script
    assert "每轮闲鱼商品上限" in script
    assert 'url.startsWith("//") ? "https:" + url : url' in script
    assert "sigmerchantimg" in script


def test_pages_build_copies_verified_snapshot_and_images(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    destination = tmp_path / "site"

    subprocess.run(
        [sys.executable, "scripts/build_pages.py", "--dest", str(destination)],
        cwd=root,
        check=True,
    )

    payload = json.loads(
        (destination / "data" / "dual-market-snapshot.json").read_text(
            encoding="utf-8"
        )
    )
    cards = payload["ready"] + payload["negative_profit"] + payload["cost_pending"]
    assert cards
    for card in cards:
        for source in ("xianyu", "wameiji"):
            relative_image = card[source]["image_url"]
            assert relative_image.startswith("assets/dual-market/")
            image_path = destination / relative_image
            assert image_path.is_file()
            with Image.open(image_path) as image:
                image.verify()
