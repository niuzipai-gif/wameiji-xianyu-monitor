#!/usr/bin/env python3
"""Recalculate current dual-market pairs from saved evidence without collecting."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from cd_monitor.core.dual_market import select_lowest_eligible
from cd_monitor.services.dual_market_profit_policy import (
    STRICT_PROFIT_POLICY_VERSION,
    strict_profit_cost_config_from_snapshot,
)
from cd_monitor.services.dual_market_service import (
    comparison_cost_breakdown,
    rebuild_current_comparison,
)
from cd_monitor.storage.sqlite import list_current_observations


def reprice_saved_pairs(db_path: str | Path) -> dict[str, Any]:
    """Append strict comparison snapshots using only current persisted observations."""

    observations = list_current_observations(db_path)
    grouped: dict[str, list[Any]] = {}
    for observation in observations:
        if observation.canonical_product_key:
            grouped.setdefault(observation.canonical_product_key, []).append(observation)

    items: list[dict[str, Any]] = []
    counts = {"eligible": 0, "below_margin": 0, "cost_pending": 0}
    for product_key in sorted(grouped):
        group = grouped[product_key]
        wameiji = select_lowest_eligible(group, source="wameiji")
        if wameiji is None:
            continue
        xianyu = select_lowest_eligible(
            (
                observation
                for observation in group
                if observation.condition_group == wameiji.condition_group
            ),
            source="xianyu",
        )
        if xianyu is None:
            continue

        config = strict_profit_cost_config_from_snapshot(wameiji.raw_snapshot_path)
        breakdown = comparison_cost_breakdown(wameiji, xianyu, config)
        outcome = rebuild_current_comparison(db_path, product_key, config)
        if outcome.comparison is None:
            continue
        counts[outcome.status] += 1
        items.append(
            {
                "canonical_product_key": product_key,
                "comparison_id": outcome.comparison.id,
                "status": outcome.status,
                "missing_fields": breakdown["missing_fields"],
                "net_profit_cny": breakdown["net_profit_cny"],
                "net_margin": breakdown["net_margin"],
            }
        )

    return {
        "policy_version": STRICT_PROFIT_POLICY_VERSION,
        "evaluated_count": len(items),
        "eligible_count": counts["eligible"],
        "below_margin_count": counts["below_margin"],
        "cost_pending_count": counts["cost_pending"],
        "items": items,
    }


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reprice saved Wameiji/Xianyu observations without network collection"
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--json-output", required=True)
    parser.add_argument("--board-output", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = reprice_saved_pairs(args.db)
    _write_json(args.json_output, result)

    # Keep the file exactly aligned with the HTTP board projection. Importing
    # this read-only projector cannot launch a collector or browser.
    from cd_monitor.web_server import _dual_market_board

    _write_json(args.board_output, _dual_market_board(args.db))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
