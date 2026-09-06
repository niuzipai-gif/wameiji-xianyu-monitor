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
    assert 'id="scanDiscoveryNowBtn"' in index
    assert "采集机状态" in index
    assert "浏览器扩展" not in index
    assert "/api/discovery/board" in script
    assert "/api/discovery/commands" in script
