from __future__ import annotations

import hashlib


def stable_hash(*parts: object) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def build_alert_hash(
    source: str,
    external_item_id: str | None,
    item_price: float,
    xianyu_price: float,
    landed_cost: float,
    decision: str,
) -> str:
    return stable_hash(
        source,
        external_item_id or "",
        int(item_price // 10 * 10),
        int(xianyu_price // 10 * 10),
        int(landed_cost // 10 * 10),
        decision,
    )
