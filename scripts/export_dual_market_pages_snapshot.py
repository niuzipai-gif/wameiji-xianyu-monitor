#!/usr/bin/env python3
"""Export the current verified dual-market board for static GitHub Pages."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from cd_monitor.pages_snapshot import (  # noqa: E402
    DownloadedImage,
    download_public_image,
    export_pages_snapshot,
)


def load_board(*, board_url: str, board_file: str) -> dict[str, object]:
    if board_file:
        payload = json.loads(Path(board_file).read_text(encoding="utf-8"))
    else:
        response = requests.get(board_url, timeout=(5, 20))
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("dual-market board must be a JSON object")
    return payload


def main(
    argv: list[str] | None = None,
    *,
    fetch_image: Callable[[str], DownloadedImage] = download_public_image,
) -> int:
    parser = argparse.ArgumentParser(
        description="Publish a verified dual-market GitHub Pages snapshot"
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--board-url",
        default="http://127.0.0.1:9890/api/dual-market/board",
    )
    source.add_argument("--board-file", default="")
    parser.add_argument("--web-dir", default="web")
    parser.add_argument("--generated-at", default="")
    args = parser.parse_args(argv)

    generated_at = (
        datetime.fromisoformat(args.generated_at)
        if args.generated_at
        else datetime.now().astimezone()
    )
    result = export_pages_snapshot(
        load_board(board_url=args.board_url, board_file=args.board_file),
        Path(args.web_dir),
        generated_at=generated_at,
        fetch_image=fetch_image,
    )
    print(
        json.dumps(
            {
                "published_comparisons": len(result.asset_paths) // 2,
                "dropped_comparison_ids": list(result.dropped_comparison_ids),
                "snapshot": str(result.snapshot_path),
                "manifest": str(result.manifest_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
