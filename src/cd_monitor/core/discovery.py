from __future__ import annotations

from dataclasses import dataclass

from cd_monitor.core.hashing import stable_hash
from cd_monitor.core.identifiers import normalize_catalog_no_compact, normalize_jan


@dataclass(slots=True)
class DiscoveryPool:
    id: int | None
    slug: str
    name: str
    media_type: str
    enabled: bool = True
    scan_interval_minutes: int = 30
    keyword_budget: int = 2
    page_budget: int = 1
    candidate_budget: int = 2
    min_profit_cny: float = 35.0
    min_margin: float = 0.25
    min_match_confidence: float = 0.75
    min_valid_xianyu_samples: int = 2
    cost_overrides_json: str = "{}"
    last_scanned_at: str | None = None
    next_run_at: str | None = None


@dataclass(slots=True)
class DiscoveryKeyword:
    id: int | None
    pool_id: int
    keyword: str
    weight: int = 1
    enabled: bool = True
    last_scanned_at: str | None = None


@dataclass(slots=True)
class DiscoveryCandidate:
    pool_id: int
    media_type: str
    identity_key: str
    title: str
    id: int | None = None
    catalog_no: str | None = None
    jan: str | None = None
    artist: str | None = None
    edition: str | None = None
    source_item_id: str | None = None
    source_url: str | None = None
    source_price: float = 0.0
    source_currency: str = "JPY"
    availability: str = "unknown_but_visible"
    status: str = "active"
    observation_count: int = 1
    missing_scan_count: int = 0
    last_xianyu_checked_at: str | None = None
    raw_text: str | None = None
    detail_verified: bool = False


@dataclass(slots=True)
class DiscoveryRun:
    id: int | None
    pool_id: int
    source: str
    status: str
    keyword_id: int | None = None
    keyword: str | None = None
    discovered_count: int = 0
    candidate_count: int = 0
    evaluated_count: int = 0
    error_type: str | None = None
    error_message: str | None = None
    screenshot_path: str | None = None
    raw_snapshot_path: str | None = None


@dataclass(slots=True)
class CollectorCommand:
    id: int | None
    command_type: str
    payload_json: str = "{}"
    status: str = "pending"
    result_json: str | None = None
    dedupe_key: str | None = None


def build_identity_key(
    *,
    catalog_no: str | None,
    jan: str | None,
    external_item_id: str | None,
    title: str | None,
    artist: str | None = None,
    edition: str | None = None,
) -> str:
    """Return a stable candidate key, favouring exact matching evidence."""

    source_id = str(external_item_id or "").strip()
    if source_id:
        return f"source:{source_id}"
    compact_catalog = normalize_catalog_no_compact(catalog_no)
    if compact_catalog:
        return f"catalog:{compact_catalog}"
    normalized_jan = normalize_jan(jan)
    if normalized_jan:
        return f"jan:{normalized_jan}"
    normalized = "|".join(
        text for value in (artist, title, edition) if (text := _normalized_text(value))
    )
    if not normalized:
        raise ValueError("A candidate needs an identifier or a title")
    return f"title:{stable_hash(normalized)}"


def _normalized_text(value: str | None) -> str:
    return " ".join(str(value or "").strip().casefold().split())
