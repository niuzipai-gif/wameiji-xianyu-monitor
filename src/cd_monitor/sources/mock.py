from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cd_monitor.core.identifiers import normalize_catalog_no_compact
from cd_monitor.core.models import MarketItem, WatchItem, XianyuPriceSample
from cd_monitor.sources.base import WameijiSourceAdapter, XianyuSourceAdapter


class MockWameijiAdapter(WameijiSourceAdapter):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def watch_item(self, catalog_no: str) -> WatchItem:
        return WatchItem(catalog_no=catalog_no)

    def search(self, watch_item: WatchItem, limit: int = 10) -> list[MarketItem]:
        rows = _load_json_list(self.path)
        results = [MarketItem(**{**row, "source": row.get("source") or "mock"}) for row in rows]
        filtered = [item for item in results if _market_matches(item, watch_item)]
        return filtered[:limit]


class MockXianyuAdapter(XianyuSourceAdapter):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def search_samples(self, watch_item: WatchItem, limit: int = 15) -> list[XianyuPriceSample]:
        rows = _load_json_list(self.path)
        rows = [row for row in rows if _row_matches(row, watch_item)]
        samples = [XianyuPriceSample(catalog_no=watch_item.catalog_no, **_sample_payload(row)) for row in rows]
        filtered = [sample for sample in samples if _sample_matches(sample, watch_item)]
        return filtered[:limit]


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError(f"Mock file must contain a JSON list: {path}")
    return data


def _sample_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload.pop("catalog_no", None)
    return payload


def _market_matches(item: MarketItem, watch_item: WatchItem) -> bool:
    haystack = " ".join(
        value
        for value in [item.catalog_no, item.jan, item.title, item.raw_text]
        if value
    )
    return _text_matches(haystack, watch_item)


def _sample_matches(sample: XianyuPriceSample, watch_item: WatchItem) -> bool:
    haystack = " ".join(
        value
        for value in [sample.catalog_no, sample.title, sample.raw_text]
        if value
    )
    return _text_matches(haystack, watch_item)


def _text_matches(text: str, watch_item: WatchItem) -> bool:
    if watch_item.jan and watch_item.jan in text:
        return True
    compact = normalize_catalog_no_compact(watch_item.catalog_no)
    return bool(compact and compact in normalize_catalog_no_compact(text))


def _row_matches(row: dict[str, Any], watch_item: WatchItem) -> bool:
    haystack = " ".join(str(value) for value in row.values() if value is not None)
    return _text_matches(haystack, watch_item)
