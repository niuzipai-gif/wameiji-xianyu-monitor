from __future__ import annotations

import subprocess
import sys
from pathlib import Path


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
