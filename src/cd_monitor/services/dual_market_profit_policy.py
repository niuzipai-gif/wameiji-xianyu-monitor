"""Versioned, evidence-backed profit policy for Wameiji to Xianyu resale."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from cd_monitor.core.dual_market import DualMarketCostConfig


STRICT_PROFIT_POLICY_VERSION = "wameiji-xianyu-net-v1"

_IGNORED_TAGS = frozenset({"script", "style", "noscript", "svg", "template"})
_ROW_LABELS = (
    "日本国内运费",
    "日本境内运费",
    "代购手续费",
    "追加手数料",
    "服务费",
    "服務費",
    "加固",
    "拍照",
    "检查费",
    "檢查費",
    "保障",
    "合单费",
    "合單費",
)
_SELLER_PAID_MARKERS = ("卖家承担", "賣家承擔", "出品者負担", "送料込み", "送料無料")


@dataclass(frozen=True, slots=True)
class WameijiCostEvidence:
    exchange_rate_cny_per_jpy: float | None
    proxy_fee_jpy: float | None
    japan_domestic_shipping_jpy: float | None


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.casefold() in _IGNORED_TAGS:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in _IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value and not self._ignored_depth:
            self.parts.append(value)


def _visible_text(html: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(str(html or ""))
    parser.close()
    return " ".join(parser.parts)


def _first_positive_number(match: re.Match[str] | None) -> float | None:
    if match is None:
        return None
    value = float(match.group(1).replace(",", ""))
    return value if value > 0 else None


def _labeled_jpy_fee(text: str, labels: tuple[str, ...], *, seller_paid_zero: bool) -> float | None:
    label_match = re.search("|".join(re.escape(label) for label in labels), text)
    if label_match is None:
        return None
    nearby = text[label_match.end() : label_match.end() + 180]
    next_label = re.search("|".join(re.escape(label) for label in _ROW_LABELS), nearby)
    row_text = nearby[: next_label.start()] if next_label else nearby
    if seller_paid_zero and any(marker in row_text for marker in _SELLER_PAID_MARKERS):
        return 0.0
    return _first_positive_number(re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*日元", row_text))


def parse_wameiji_cost_evidence(html: str) -> WameijiCostEvidence:
    """Extract only explicitly labelled costs from one saved Wameiji detail page."""

    text = _visible_text(html)
    exchange_rate = _first_positive_number(
        re.search(
            r"(?:挖煤姬)?汇率\s*[:：]?\s*1\s*(?:日元|JPY)\s*(?:≈|=|约|約)?\s*"
            r"([0-9]+(?:\.[0-9]+)?)\s*(?:人民币|人民幣|CNY)",
            text,
            re.IGNORECASE,
        )
    )
    return WameijiCostEvidence(
        exchange_rate_cny_per_jpy=exchange_rate,
        proxy_fee_jpy=_labeled_jpy_fee(text, ("代购手续费",), seller_paid_zero=False),
        japan_domestic_shipping_jpy=_labeled_jpy_fee(
            text,
            ("日本国内运费", "日本境内运费"),
            seller_paid_zero=True,
        ),
    )


def strict_profit_cost_config(evidence: WameijiCostEvidence) -> DualMarketCostConfig:
    """Build the user-approved immutable resale policy from one detail snapshot."""

    return DualMarketCostConfig(
        exchange_rate_cny_per_jpy=evidence.exchange_rate_cny_per_jpy,
        japan_domestic_shipping_jpy=evidence.japan_domestic_shipping_jpy,
        proxy_fee_jpy=evidence.proxy_fee_jpy,
        international_shipping_per_item_cny=15.0,
        china_reship_cny=5.0,
        packaging_cny=2.0,
        after_sale_reserve_cny=0.0,
        risk_reserve_cny=0.0,
        tax_cny=0.0,
        sales_fee_rate=0.016,
        sales_fee_cap_cny=None,
        sales_fee_uncapped=True,
        minimum_net_margin=0.25,
        policy_version=STRICT_PROFIT_POLICY_VERSION,
    )


def strict_profit_cost_config_from_snapshot(
    snapshot_path: str | Path | None,
) -> DualMarketCostConfig:
    """Read a saved detail page; an unreadable path becomes pending, never guessed."""

    html = ""
    if snapshot_path:
        try:
            html = Path(snapshot_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            html = ""
    return strict_profit_cost_config(parse_wameiji_cost_evidence(html))
