from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def save_snapshot(snapshot_dir: str | Path, source: str, payload: Any) -> Path:
    directory = Path(snapshot_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"{source}-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
