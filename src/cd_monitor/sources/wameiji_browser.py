from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from cd_monitor.core.identifiers import (
    extract_catalog_candidates,
    extract_jan_candidates,
    normalize_catalog_no_compact,
)
from cd_monitor.core.models import AdapterStatus, MarketItem, WatchItem
from cd_monitor.core.product_images import is_usable_product_image, normalize_product_image_url
from cd_monitor.sources.base import BrowserHarnessAdapter


_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class WameijiBrowserAdapter(BrowserHarnessAdapter):


    def search_status(self, watch_item: WatchItem) -> AdapterStatus:
        if not self.enabled:
            return AdapterStatus(
                status="disabled",
                error_type="browser_disabled",
                error_message="browser.enabled=false; real Wameiji browser reads are not run.",
            )
        return AdapterStatus(
            status="human_required",
            error_type="not_configured",
            error_message=(
                "Configure a real Chrome profile and browser tool before reading Wameiji pages. "
                "Stop on captcha, security checks, login expiry, or any purchase action."
            ),
            search_entry_url=f"https://meruki.cn/search?keywords={quote_plus(watch_item.catalog_no)}",
            capture_instruction=(
                "Open the Wameiji search page in a logged-in visible browser, search the catalog/JAN, "
                "then provide the visible search result HTML through Evaluate Pasted HTML or evaluate-html."
            ),
        )

    def parse_search_html(self, html: str, watch_item: WatchItem) -> AdapterStatus:
        if _requires_human(html):
            return AdapterStatus(
                status="human_required",
                error_type="security_check",
                error_message="Captcha, security check, or login challenge detected.",
            )
        parser = _WameijiCardParser()
        parser.feed(html)
        had_card_items = bool(parser.items)
        if had_card_items and _is_precise_identifier_query(watch_item.catalog_no):
            # A precise product lookup should reject related cards. A generic
            # discovery search is different: its result cards often use a
            # shorter title that does not repeat every search token. Keep
            # those cards and let the pool-specific media gate decide whether
            # they are relevant before any Xianyu lookup is made.
            parser.items = [
                item
                for item in parser.items
                if _matches_search_query(item.title, watch_item.catalog_no)
            ]
        if not had_card_items and not parser.items:
            parser.items = _extract_generic_items(html, watch_item)
        return AdapterStatus(status="ok", items=parser.items)

    def parse_detail_html(self, html: str, search_item: MarketItem) -> AdapterStatus:
        """Parse one Wameiji listing detail page into a verified purchase item.

        Search cards can truncate a title, omit an identifier, and display a
        stale price.  The caller supplies the card only to retain its stable
        source URL/id; title, price, availability and identifiers must all be
        obtained again from the opened detail page.
        """
        if _requires_human(html):
            return AdapterStatus(
                status="human_required",
                error_type="security_check",
                error_message="Captcha, security check, or login challenge detected.",
            )
        if _is_removed_listing_page(html):
            # A deleted Wameiji URL renders generic recommendation cards under
            # the same site container. Keep the original identity only to mark
            # it unavailable; never mistake one of those cards for the source.
            return AdapterStatus(
                status="ok",
                items=[
                    replace(
                        search_item,
                        availability="sold_out",
                        detail_verified=True,
                        price_cny_display=None,
                        raw_text="Wameiji detail page reports that this listing was removed.",
                    )
                ],
            )
        parser = _WameijiDetailParser()
        parser.feed(html)
        if not parser._scope_seen and _is_generic_listing_fallback_page(html):
            return AdapterStatus(
                status="ok",
                items=[
                    replace(
                        search_item,
                        availability="sold_out",
                        detail_verified=True,
                        price_cny_display=None,
                        raw_text=(
                            "Wameiji detail URL resolved to a generic listings page; "
                            "the source listing is no longer purchasable."
                        ),
                    )
                ],
            )
        title = " ".join(parser._title_parts).strip()
        if title and parser.availability() == "sold_out":
            return AdapterStatus(
                status="ok",
                items=[
                    replace(
                        search_item,
                        title=title,
                        availability="sold_out",
                        detail_verified=True,
                        price_cny_display=None,
                        raw_text=" ".join(parser.text_parts),
                    )
                ],
            )
        item = _detail_item_from_parser(
            parser,
            search_item,
            json_ld_image_url=_matching_json_ld_product_image(html, search_item),
        )
        if item is None:
            return AdapterStatus(
                status="human_required",
                error_type="detail_parse_failed",
                error_message="Wameiji detail page did not expose a product title and JPY price.",
            )
        return AdapterStatus(status="ok", items=[item])


class _WameijiCardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[MarketItem] = []
        self._in_card = False
        self._card_depth = 0
        self._card_tag: str | None = None
        self._capture: str | None = None
        self._capture_tag: str | None = None
        self._current: dict[str, str] = {}
        self._text_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = set(str(attr.get("class") or "").split())
        started_card = False
        # The current meruki.cn DOM uses <a class="goods-item"> cards;
        # older snapshots used a data-item-card wrapper. Support both.
        if "data-item-card" in attr or "goods-item" in classes:
            self._in_card = True
            self._card_depth = 1
            self._card_tag = tag
            self._current = {}
            self._text_buf = []
            started_card = True
        if not self._in_card:
            return
        if not started_card and tag not in _VOID_TAGS:
            self._card_depth += 1
        if tag == "a" and attr.get("href"):
            self._current.setdefault("url", attr["href"] or "")
            # A goods-item root is the link itself; its title is in the
            # nested .goods-name element. Legacy cards keep the title in <a>.
            if not (started_card and "goods-item" in classes):
                self._capture = "title"
                self._capture_tag = tag
        if "goods-name" in classes:
            self._capture = "title"
            self._capture_tag = tag
        elif "data-price" in attr or "goods-price" in classes or "price-com" in classes:
            self._capture = "price"
            self._capture_tag = tag
        elif "data-source-site" in attr:
            self._capture = "source_site"
            self._capture_tag = tag
        elif tag == "img" and attr.get("src"):
            self._current["image_url"] = attr["src"] or ""

    def handle_data(self, data: str) -> None:
        if not self._in_card:
            return
        text_chunk = data.strip()
        if not text_chunk:
            return
        # Always accumulate for raw_text (covers badges/status/notes)
        self._text_buf.append(text_chunk)
        # Targeted capture for data-price/data-source-site/<a> title
        if self._capture:
            self._current[self._capture] = (self._current.get(self._capture, "") + text_chunk).strip()

    def handle_endtag(self, tag: str) -> None:
        if self._capture_tag == tag:
            self._capture = None
            self._capture_tag = None
        if not self._in_card:
            return
        self._card_depth -= 1
        if self._card_depth > 0:
            return
        if self._card_depth == 0:
            title = self._current.get("title", "")
            price = _parse_price(self._current.get("price", "0"))
            raw_text = " ".join(list(self._current.values()) + self._text_buf)
            if title and price >= 0:
                self.items.append(
                    MarketItem(
                        source="wameiji",
                        title=title,
                        price=price,
                        currency="JPY",
                        source_site=(
                            self._current.get("source_site")
                            or _source_site_from_url(self._current.get("url"))
                        ),
                        external_item_id=_external_item_id(self._current.get("url")),
                        url=self._current.get("url"),
                        image_url=self._current.get("image_url"),
                        availability=_detect_availability(raw_text),
                        condition_text=_detect_condition_from_text(raw_text),
                        fees_hint=_detect_fees_hint(raw_text),
                        raw_text=raw_text,
                    )
                )
            self._in_card = False
            self._card_depth = 0
            self._card_tag = None
            self._capture = None
            self._capture_tag = None
            self._current = {}
            self._text_buf = []


class _WameijiDetailParser(HTMLParser):
    """Extract evidence from the product area, rather than the entire page.

    Wameiji detail pages also render a footer (``All Rights Reserved``) and
    recommendation cards.  Treating every text node or every ``price-com``
    node as product evidence turns that footer into a false reservation and
    can concatenate several unrelated prices.  A detail record is trustworthy
    only when its title, price and stock signal come from the primary detail
    component.
    """

    _TITLE_CLASSES = {"goods-name", "goods-title", "item-title", "product-title"}
    _DETAIL_ROOT_CLASSES = {
        "goods-detail",
        "product-detail",
        "item-detail",
        "mercari-detail",
        "rakuma-detail",
        "paypay-detail",
        "yahoo-detail",
        "yahoo-auction-detail",
        "surugaya-detail",
        "bookoff-detail",
        "market-detail",
        "street-detail",
        "paypay",
        "goods",
    }
    _EXCLUDED_CLASSES = {
        "other-item",
        "related-item",
        "recommend-item",
        "recommendation-item",
        "similar-item",
    }
    _IGNORED_TAGS = {"noscript", "script", "style", "svg", "template"}
    _PURCHASE_ACTION_CLASSES = {"buy-now", "cart", "add-to-cart", "purchase"}
    _IMAGE_ATTRIBUTE_ORDER = ("data-original", "data-src", "data-lazy-src", "src")
    _NON_PRODUCT_IMAGE_CLASS_TOKENS = (
        "logo",
        "avatar",
        "placeholder",
        "icon",
        "sold",
        "badge",
        "qrcode",
        "qr-code",
    )
    _AVAILABLE_TEXT_TOKENS = (
        "available",
        "贩売中",
        "在庫あり",
        "可购买",
        "可購入",
        "出品中",
        "在售",
        "在库",
    )

    def __init__(self) -> None:
        super().__init__()
        self.text_parts: list[str] = []
        self._title_parts: list[str] = []
        self._price_parts: list[str] = []
        self._tag_stack: list[set[str]] = []
        self._ignored_depth = 0
        self._scope_depth: int | None = None
        self._scope_seen = False
        self._excluded_depth: int | None = None
        self._title_depth: int | None = None
        self._price_capture_parts: dict[int, list[str]] = {}
        self._price_capture_priority: dict[int, int] = {}
        self._price_candidates: list[tuple[int, int, str]] = []
        self._price_sequence = 0
        self._status_capture_parts: dict[int, list[str]] = {}
        self._status_parts: list[str] = []
        self._availability_signals: list[str] = []
        self._has_purchase_action = False
        self._product_image_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return

        attr = dict(attrs)
        classes = set(str(attr.get("class") or "").split())
        if tag not in _VOID_TAGS:
            self._tag_stack.append(classes)

        if not self._scope_seen and classes.intersection(self._DETAIL_ROOT_CLASSES):
            self._scope_seen = True
            self._scope_depth = len(self._tag_stack)
        if not self._in_scope():
            return
        if self._excluded_depth is not None:
            return
        if classes.intersection(self._EXCLUDED_CLASSES) or any(
            "recommend" in class_name or "related" in class_name
            for class_name in classes
        ):
            if tag not in _VOID_TAGS:
                self._excluded_depth = len(self._tag_stack)
            return

        depth = len(self._tag_stack)
        if tag == "h1" or (
            not self._title_parts and classes.intersection(self._TITLE_CLASSES)
        ):
            if self._title_depth is None:
                self._title_depth = depth

        if "price-com" in classes or "goods-price-value" in classes or "data-price" in attr:
            self._price_capture_parts[depth] = []
            self._price_capture_priority[depth] = (
                0 if any("price-box-total" in parent for parent in self._tag_stack) else 1
            )

        class_text = " ".join(classes).lower()
        if tag == "img":
            self._capture_product_image(attr, class_text)
        if any(token in class_text for token in ("sold", "unavailable", "out-of-stock")):
            self._availability_signals.append("sold out")
        elif "reserved" in class_text:
            self._availability_signals.append("reserved")

        if tag in {"button", "a"} and classes.intersection(self._PURCHASE_ACTION_CLASSES):
            disabled = (
                "disabled" in attr
                or str(attr.get("aria-disabled") or "").lower() == "true"
                or "disabled" in classes
            )
            if disabled:
                self._availability_signals.append("sold out")
            else:
                self._has_purchase_action = True

        if any(
            token in class_text
            for token in ("availability", "stock", "sale-status", "sell-status")
        ):
            self._status_capture_parts[depth] = []

    def _capture_product_image(self, attr: dict[str, str | None], class_text: str) -> None:
        """Keep only image candidates inside the verified detail component."""

        if any(token in class_text for token in self._NON_PRODUCT_IMAGE_CLASS_TOKENS):
            return
        for name in self._IMAGE_ATTRIBUTE_ORDER:
            value = str(attr.get(name) or "").strip()
            if value and value not in self._product_image_urls:
                self._product_image_urls.append(value)

    def product_image_url(self, detail_url: str | None) -> str | None:
        """Return the first non-chrome image supplied by the detail component."""

        base_url = str(detail_url or "").strip()
        if base_url.startswith("/"):
            base_url = "https://meruki.cn" + base_url
        for value in self._product_image_urls:
            normalized = normalize_product_image_url(value, base_url=base_url or None)
            if normalized and is_usable_product_image(normalized):
                return normalized
        return None

    def handle_data(self, data: str) -> None:
        if self._ignored_depth or not self._in_scope() or self._excluded_depth is not None:
            return
        value = data.strip()
        if not value:
            return
        self.text_parts.append(value)
        if self._title_depth is not None:
            self._title_parts.append(value)
        for parts in self._price_capture_parts.values():
            parts.append(value)
        for parts in self._status_capture_parts.values():
            parts.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth or tag in _VOID_TAGS or not self._tag_stack:
            return

        depth = len(self._tag_stack)
        if self._title_depth == depth:
            self._title_depth = None
        if depth in self._price_capture_parts:
            text = " ".join(self._price_capture_parts.pop(depth)).strip()
            priority = self._price_capture_priority.pop(depth)
            if text:
                self._price_candidates.append((priority, self._price_sequence, text))
                self._price_sequence += 1
        if depth in self._status_capture_parts:
            self._status_parts.extend(self._status_capture_parts.pop(depth))

        self._tag_stack.pop()
        if self._excluded_depth is not None and len(self._tag_stack) < self._excluded_depth:
            self._excluded_depth = None
        if self._scope_depth is not None and len(self._tag_stack) < self._scope_depth:
            self._scope_depth = None

    def _in_scope(self) -> bool:
        return self._scope_depth is not None and len(self._tag_stack) >= self._scope_depth

    def price(self) -> float:
        for _, _, text in sorted(self._price_candidates):
            price = _parse_price(text)
            if price > 0:
                self._price_parts = text.split()
                return price
        return 0.0

    def availability(self) -> str:
        explicit_status = " ".join(self._availability_signals + self._status_parts)
        explicit_availability = _detect_availability(explicit_status)
        if explicit_availability != "unknown_but_visible":
            return explicit_availability
        scoped_text = " ".join(self.text_parts).lower()
        scoped_availability = _detect_availability(scoped_text)
        if scoped_availability != "unknown_but_visible":
            return scoped_availability
        if self._has_purchase_action or any(
            token in scoped_text for token in self._AVAILABLE_TEXT_TOKENS
        ):
            return "available"
        return "unknown_but_visible"


def _detail_item_from_parser(
    parser: _WameijiDetailParser,
    search_item: MarketItem,
    *,
    json_ld_image_url: str | None = None,
) -> MarketItem | None:
    title = " ".join(parser._title_parts).strip()
    price = parser.price()
    raw_text = " ".join(parser.text_parts)
    if not title or price <= 0:
        return None
    # A search-card identifier is only a hint. Do not carry it into the
    # comparison record when the opened detail page does not confirm it.
    catalog_no = _first_detail_catalog_no(raw_text)
    jan = _first_detail_jan(raw_text)
    return MarketItem(
        source="wameiji",
        title=title,
        price=price,
        currency="JPY",
        source_site=search_item.source_site or _detect_source_site(raw_text),
        external_item_id=search_item.external_item_id,
        catalog_no=catalog_no,
        jan=jan,
        # Do not carry a search-card conversion amount into a verified detail
        # record: it may be stale and does not include page-specific fees.
        price_cny_display=None,
        url=search_item.url,
        image_url=json_ld_image_url or parser.product_image_url(search_item.url),
        availability=parser.availability(),
        condition_text=_detect_condition_from_text(raw_text),
        fees_hint=_detect_fees_hint(raw_text),
        raw_text=raw_text,
        detail_verified=True,
        japan_domestic_shipping_jpy=_extract_labeled_jpy_fee(
            raw_text, "日本国内运费"
        ),
        proxy_fee_jpy=_extract_labeled_jpy_fee(raw_text, "代购手续费"),
    )


_JSON_LD_SCRIPT_PATTERN = re.compile(
    r"<script\b[^>]*\btype\s*=\s*(?:[\"'])?application/ld\+json(?:[\"'])?[^>]*>"
    r"(?P<payload>.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)


def _matching_json_ld_product_image(html: str, search_item: MarketItem) -> str | None:
    """Return a usable JSON-LD image only for this exact detail listing."""

    external_item_id = str(search_item.external_item_id or "").strip()
    if not external_item_id:
        return None
    base_url = str(search_item.url or "").strip()
    if base_url.startswith("/"):
        base_url = "https://meruki.cn" + base_url
    for match in _JSON_LD_SCRIPT_PATTERN.finditer(html):
        try:
            payload = json.loads(match.group("payload"))
        except json.JSONDecodeError:
            continue
        for product in _json_ld_product_nodes(payload):
            if not _json_ld_product_matches_item(product, external_item_id):
                continue
            for image in _json_ld_image_values(product.get("image")):
                normalized = normalize_product_image_url(image, base_url=base_url or None)
                if normalized and is_usable_product_image(normalized):
                    return normalized
    return None


def _json_ld_product_nodes(payload: object) -> list[dict[str, object]]:
    if isinstance(payload, list):
        nodes: list[dict[str, object]] = []
        for entry in payload:
            nodes.extend(_json_ld_product_nodes(entry))
        return nodes
    if not isinstance(payload, dict):
        return []
    nodes = [payload] if _json_ld_has_product_type(payload.get("@type")) else []
    graph = payload.get("@graph")
    if isinstance(graph, list):
        for entry in graph:
            nodes.extend(_json_ld_product_nodes(entry))
    return nodes


def _json_ld_has_product_type(value: object) -> bool:
    values = value if isinstance(value, list) else [value]
    return any(str(entry).strip().lower() == "product" for entry in values)


def _json_ld_product_matches_item(product: dict[str, object], external_item_id: str) -> bool:
    item_identities = _listing_identity_candidates(external_item_id)
    if item_identities.intersection(
        _listing_identity_candidates(str(product.get("sku") or ""))
    ):
        return True
    for key in ("@id", "url", "mainEntityOfPage"):
        value = product.get(key)
        if isinstance(value, dict):
            value = value.get("@id") or value.get("url")
        if item_identities.intersection(_listing_identity_candidates(str(value or ""))):
            return True
    return False


def _listing_identity_candidates(value: str) -> set[str]:
    """Normalize direct ids and Wameiji's URL-encoded marketplace ids."""

    raw = unquote(str(value or "").strip()).strip().rstrip("/")
    if not raw:
        return set()
    parsed = urlparse(raw)
    path = parsed.path if parsed.scheme and parsed.netloc else raw
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    candidates = {raw.lower()}
    if parts:
        candidates.add(parts[-1].lower())
    if len(parts) >= 2:
        candidates.add("/".join(parts[-2:]).lower())
    return candidates


def _json_ld_image_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(value.get(key) or "") for key in ("url", "contentUrl")]
    if isinstance(value, list):
        values: list[str] = []
        for entry in value:
            values.extend(_json_ld_image_values(entry))
        return values
    return []


def _first_detail_catalog_no(text: str) -> str | None:
    for candidate in extract_catalog_candidates(text):
        prefix, _, suffix = candidate.partition("-")
        if len(prefix) >= 3 and len(suffix) >= 3:
            return candidate
    return None


def _first_detail_jan(text: str) -> str | None:
    for candidate in extract_jan_candidates(text):
        if len(candidate) == 13 and candidate.startswith(("45", "49")):
            return candidate
    return None


def _parse_price(value: str) -> float:
    digits = re.sub(r"[^\d.]", "", value)
    return float(digits) if digits else 0.0


def _extract_labeled_jpy_fee(text: str, label: str) -> float | None:
    """Read a JPY fee only when it follows its detail-page label.

    Wameiji renders the product price, domestic shipping and proxy fee as
    separate ``sku-item`` rows.  Looking for a bare number would confuse one
    row with another, so the label is mandatory and the search stops at the
    first nearby ``日元`` value.
    """
    if not text or not label:
        return None
    label_match = re.search(re.escape(label), text)
    if label_match is None:
        return None

    # ``get_text`` flattens individual sku rows.  Restrict the read to this
    # row so a later fee (for example ``代购手续费 200日元``) cannot become the
    # value for a seller-borne domestic-shipping row.
    nearby = text[label_match.end() : label_match.end() + 160]
    next_label = re.search(
        r"(?:日本国内运费|代购手续费|追加手数料|サービス料|加固|拍照|检查费|保障|合单费)",
        nearby,
    )
    row_text = nearby[: next_label.start()] if next_label else nearby
    if label == "日本国内运费" and any(
        marker in row_text
        for marker in ("卖家承担", "出品者負担", "送料込み", "送料無料")
    ):
        return 0.0

    amount_match = re.search(r"([0-9][0-9,]*)\s*日元", row_text)
    if amount_match is None:
        return None
    return float(amount_match.group(1).replace(",", ""))


def _catalog_in_text(text: str, catalog_no: str) -> bool:
    """Match a catalog/JAN anywhere in a title without URL/first-token bias."""
    compact_text = re.sub(r"[^a-z0-9]", "", (text or "").lower())
    compact_catalog = re.sub(r"[^a-z0-9]", "", (catalog_no or "").lower())
    return bool(compact_catalog) and compact_catalog in compact_text


def _is_precise_identifier_query(query: str) -> bool:
    return bool(extract_catalog_candidates(query) or extract_jan_candidates(query))


def _matches_search_query(text: str, query: str) -> bool:
    if _is_precise_identifier_query(query):
        return _catalog_in_text(text, query)
    normalized_query = " ".join((query or "").casefold().split())
    normalized_text = " ".join((text or "").casefold().split())
    return bool(normalized_query) and normalized_query in normalized_text



def _detect_condition_from_text(text: str) -> str | None:
    """condense helper for _WameijiCardParser.

    _detect_condition_text() takes a list[str]; for the card parser we already
    have a joined string, so we split by whitespace and reuse the same keyword table.
    """
    labeled = re.search(
        r"(?:^|\s)(?:商品)?(?:状態|状态|狀態)\s*[:：]?\s*"
        r"(?P<value>.{1,160}?)"
        r"(?=\s+(?:数量|個数|价格|価格|日本国内运费|日本国内運費|"
        r"代购手续费|代購手續費|店铺|店鋪|ショップ|加入购物车|"
        r"加入購物車|立即购买|立即購買)(?:\s|$)|$)",
        text,
        re.IGNORECASE,
    )
    if labeled is not None:
        value = " ".join(labeled.group("value").split()).strip()
        if value:
            return value
    tokens = text.split() if text else []
    return _detect_condition_text(tokens)


# ---- 费用提示检测（spec §3.1）------------------------------------------
_FEES_HINT_TOKENS: list[tuple[str, str]] = [
    ("代购手续费", "proxy_fee"),
    ("追加手数料", "add_on"),
    ("サービス料", "service_fee"),
    ("加固", "reinforcement"),
    ("拍照", "photo"),
    ("检查费", "inspection"),
    ("保障", "insurance"),
    ("合单费", "merge"),
    ("追加料金", "add_on"),
    ("追加手数料", "add_on"),
    ("日本国内运费", "japan_domestic_shipping"),
]


def _detect_fees_hint(text: str) -> str | None:
    """从 raw_text / title 里抽费用提示（spec §3.1 费用提示）。

    返回一个短中英混合标签，例如 "proxy_fee+reinforcement"，
    以便存入 MarketItem.fees_hint 供通知与决策模块使用。
    """
    lowered = text.lower()
    flags: list[str] = []
    for token, label in _FEES_HINT_TOKENS:
        if token in text or token.lower() in lowered:
            if label not in flags:
                flags.append(label)
    if not flags:
        return None
    return "+".join(flags)


def _extract_generic_items(html: str, watch_item: WatchItem) -> list[MarketItem]:
    parser = _GenericBlockParser()
    parser.feed(html)
    query = watch_item.catalog_no
    catalog = normalize_catalog_no_compact(query)
    precise_query = _is_precise_identifier_query(query)
    items: list[MarketItem] = []
    seen: set[tuple[str, str | None, float]] = set()
    for block in parser.blocks:
        text = " ".join(block["text"])
        if not _matches_search_query(text, query):
            continue
        links = block["links"]
        if precise_query and _has_multiple_catalog_links(links, catalog):
            continue
        price = _find_price(text)
        if price <= 0:
            continue
        title = _best_link_text(links, query)
        if not title:
            continue
        url = links[0][0] if links else None
        key = (title, url, price)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            MarketItem(
                source="wameiji",
                title=title,
                price=price,
                currency="JPY",
                source_site=_detect_source_site(text),
                external_item_id=_external_item_id(url),
                url=url,
                image_url=block["images"][0] if block["images"] else None,
                availability=_detect_availability(text),
                condition_text=_detect_condition_text(block["text"]),
                fees_hint=_detect_fees_hint(text),
                raw_text=text,
            )
        )
    return items


def _external_item_id(url: str | None) -> str | None:
    """Derive a stable listing key from a Wameiji item URL when available."""
    if not url:
        return None
    value = str(url).strip()
    if not value:
        return None
    parsed = urlparse(value)
    for key in ("id", "item_id", "itemId"):
        values = parse_qs(parsed.query).get(key) or []
        if values and values[0].strip():
            return values[0].strip()[:200]
    segment = parsed.path.rstrip("/").rsplit("/", 1)[-1].strip()
    if segment and segment.lower() not in {"item", "detail", "product", "search"}:
        return segment[:200]
    return None


def _source_site_from_url(url: str | None) -> str | None:
    """Extract the underlying marketplace from a Wameiji detail path."""
    if not url:
        return None
    parts = [part for part in urlparse(str(url).strip()).path.split("/") if part]
    try:
        mall_index = parts.index("mall")
    except ValueError:
        return None
    if mall_index + 1 < len(parts):
        return parts[mall_index + 1].strip() or None
    return None

_PRICE_PATTERNS = (
    re.compile(r"(?:[¥￥]|JPY|CNY|RMB)\s*([\d][\d,]*(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"([\d][\d,]*(?:\.\d+)?)\s*(?:人民币|日元|日圓|円|元)"),
)


def _find_price(text: str) -> float:
    for pattern in _PRICE_PATTERNS:
        match = pattern.search(text)
        if match:
            return _parse_price(match.group(1))
    return 0.0


def _detect_availability(text: str) -> str:
    lowered = text.lower()
    if any(
        token in lowered
        for token in [
            "sold out",
            "soldout",
            "已售",
            "已卖",
            "售罄",
            "下架",
            "売り切れ",
            "売切",
            "売約済",
            "在庫なし",
            "販売終了",
        ]
    ):
        return "sold_out"
    if any(
        token in lowered
        for token in [
            "reserved",
            "専用",
            "取り置き",
            "预定",
            "予約",
        ]
    ):
        return "reserved"
    if any(
        token in lowered
        for token in [
            "available",
            "販売中",
            "在庫あり",
            "可购买",
            "可購入",
            "出品中",
            "在售",
            "在库",
        ]
    ):
        return "available"
    return "unknown_but_visible"


def _detect_source_site(text: str) -> str | None:
    lowered = text.lower()
    source_tokens = [
        ("mercari", ["mercari", "メルカリ", "煤炉"]),
        ("rakuma", ["rakuma", "ラクマ"]),
        ("yahoo_auction", ["yahoo", "ヤフオク", "paypayフリマ"]),
        ("bookoff", ["bookoff", "ブックオフ"]),
        ("surugaya", ["surugaya", "駿河屋", "骏河屋"]),
        ("jdirectitems", ["jdirectitems", "jdirect"]),
        ("amiami", ["amiami", "あみあみ"]),
        ("lashinbang", ["lashinbang", "らしんばん"]),
    ]
    for source, tokens in source_tokens:
        if any(token.lower() in lowered for token in tokens):
            return source
    return None


def _detect_condition_text(parts: list[str]) -> str | None:
    condition_tokens = [
        "動作未確認",
        "動作確認未",
        "未動作確認",
        "箱潰れ",
        "箱つぶれ",
        "盤傷",
        "ケース割れ",
        "破損",
        "傷",
        "スレ",
        "汚れ",
        "使用感",
        "ジャンク",
        "不良",
        "瑕疵",
        "裂",
        "整体状态不佳",
        "状态不佳",
        "品相不佳",
    ]
    for part in parts:
        if any(token in part for token in condition_tokens):
            return part.strip()
    return None


def _best_link_text(links: list[tuple[str | None, str]], query: str) -> str:
    for _, text in links:
        if _matches_search_query(text, query):
            return text.strip()
    return links[0][1].strip() if links else ""


def _has_multiple_catalog_links(links: list[tuple[str | None, str]], catalog: str) -> bool:
    catalog_links = {
        (href, text.strip())
        for href, text in links
        if catalog in normalize_catalog_no_compact(text)
    }
    return len(catalog_links) > 1


class _GenericBlockParser(HTMLParser):
    block_tags = {"div", "article", "li", "section"}

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[dict[str, list]] = []
        self._stack: list[dict[str, list]] = []
        self._current_href: str | None = None
        self._current_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag in self.block_tags:
            self._stack.append({"text": [], "links": [], "images": []})
        if tag == "a":
            self._current_href = attr.get("href")
            self._current_link_text = []
            link_label = _first_attr_text(attr, "title", "aria-label")
            if link_label:
                self._append_text(link_label)
                self._current_link_text.append(link_label)
        if tag == "img" and attr.get("src"):
            for block in self._stack:
                block["images"].append(attr["src"])
        elif tag == "img":
            lazy_src = _first_attr_text(attr, "data-src", "data-original", "data-lazy-src")
            if lazy_src:
                for block in self._stack:
                    block["images"].append(lazy_src)
        attr_text = _first_attr_text(attr, "aria-label", "title", "alt")
        if attr_text:
            self._append_text(attr_text)

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        for block in self._stack:
            block["text"].append(text)
        if self._current_href is not None:
            self._current_link_text.append(text)

    def _append_text(self, text: str) -> None:
        value = text.strip()
        if not value:
            return
        for block in self._stack:
            block["text"].append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_href is not None:
            text = " ".join(self._current_link_text).strip()
            if text:
                for block in self._stack:
                    block["links"].append((self._current_href, text))
            self._current_href = None
            self._current_link_text = []
        if tag in self.block_tags and self._stack:
            block = self._stack.pop()
            self.blocks.append(block)


def _first_attr_text(attr: dict[str, str | None], *keys: str) -> str:
    for key in keys:
        value = attr.get(key)
        if value and value.strip():
            return value.strip()
    return ""


_HARD_SECURITY_MARKERS = (
    "403 forbidden",
    "captcha",
    "cloudflare",
    "rgv587_error",
    "fail_sys_user_validate",
    "____tmd____",
    "x5step",
)
_TITLE_SECURITY_MARKERS = (
    "安全验证",
    "驗證",
    "登录失效",
    "請完成",
    "请完成",
    "滑块",
    "验证码",
    "風控",
    "风控",
    "验证失败",
    "需要登录",
    "请登录",
)
_HTML_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)


def _requires_human(html: str) -> bool:
    lowered = html.lower()
    if any(token in lowered for token in _HARD_SECURITY_MARKERS):
        return True
    # Product pages contain full seller descriptions. Phrases such as “验证码”
    # may be ordinary listing text, so accept softer markers only when the
    # document title itself identifies a challenge or login page.
    title_match = _HTML_TITLE_RE.search(html)
    if title_match is None:
        return False
    title = re.sub(r"<[^>]+>", " ", title_match.group(1)).casefold()
    return any(token in title for token in _TITLE_SECURITY_MARKERS)


_REMOVED_LISTING_CLASSES = (
    "common-null",
    "nonsupport-text",
    "listing-removed",
    "item-removed",
    "product-removed",
)
_REMOVED_LISTING_MARKERS = (
    "商品删除",
    "商品已删除",
    "商品不存在",
    "商品已下架",
    "商品已移除",
    "该商品已删除",
    "商品已失效",
    "item has been removed",
    "listing has been removed",
    "product has been removed",
    "this item is unavailable",
    "商品は削除されました",
    "商品が削除されました",
    "削除された商品",
)


def _is_removed_listing_page(html: str) -> bool:
    """Recognize Wameiji's detail-page deletion shell before reading its cards."""
    lowered = html.casefold()
    return (
        any(marker in lowered for marker in _REMOVED_LISTING_MARKERS)
        and any(class_name in lowered for class_name in _REMOVED_LISTING_CLASSES)
    )


def _is_generic_listing_fallback_page(html: str) -> bool:
    """Detect a stale detail route rendered as a marketplace-wide result grid.

    A real product detail is parsed only after a known primary component has
    been found.  When no such component exists and the page is entirely the
    generic ``goods-list`` shell, the original listing cannot be bought; do
    not mistake one of the unrelated cards for the requested product.
    """

    lowered = html.casefold()
    return all(
        marker in lowered
        for marker in ("goods-list-wrap", "goods-list", "goods-item", "goods-name")
    )

"""Wameiji 真实 Playwright 读取执行器。

行为准则（严格遵循 CODEX_PROJECT_SPEC §1.2 Browser-Harness）：
- 真实 Chrome Profile，不做 webdriver 隐身
- 同 action 最多 2 次重试；同路径 2 次失败切换策略
- 连续 3 次无法推进 → 停止并返回 human_required
- 遇到 captcha / 安全验证 / 登录失效 / Cloudflare → 立刻停止
- 不点击购买、不联系卖家、不发布商品、不绕过反自动化机制
"""

import asyncio
import logging
import random
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cd_monitor.storage.snapshots import save_snapshot


log = logging.getLogger("cd_monitor.wameiji_browser")


# ---- 常量（spec §3.1） -------------------------------------------------
DEFAULT_SEARCH_URL = "https://meruki.cn/search"
DEFAULT_PAGE_LOAD_TIMEOUT_MS = 30_000
DEFAULT_RESULT_TIMEOUT_MS = 20_000
MAX_CONSECUTIVE_FAILURES = 3
MAX_RETRIES_PER_ACTION = 2


# ---- 结果对象 ---------------------------------------------------------
@dataclass
class WameijiSearchOutcome:
    """一次 Wameiji 搜索的结果（含失败信息）。"""
    items: list[MarketItem] = field(default_factory=list)
    raw_html: str = ""
    screenshot_path: Path | None = None
    raw_snapshot_path: Path | None = None
    status: str = "ok"  # "ok" / "human_required" / "disabled" / "not_configured"
    error_type: str | None = None
    error_message: str | None = None
    search_entry_url: str | None = None


# ---- Playwright 导入（懒加载） ----------------------------------------
def _try_import_playwright() -> tuple[bool, str]:
    """尝试导入 playwright；失败时返回 (False, reason)。

    把 playwright 当作可选依赖：未安装时 runner 实例化不会失败，
    只在真正 search() 时报错 → caller 返回 human_required。
    """
    try:
        from playwright.async_api import async_playwright  # noqa: F401
        return True, ""
    except Exception as exc:  # pragma: no cover - 环境相关
        return False, f"{type(exc).__name__}: {exc}"


# ---- Runner ----------------------------------------------------------
class WameijiPlaywrightRunner:
    """Wameiji 真实浏览器读取执行器（基于 Playwright）。"""

    MAX_CONSECUTIVE_FAILURES = MAX_CONSECUTIVE_FAILURES
    MAX_RETRIES_PER_ACTION = MAX_RETRIES_PER_ACTION

    def __init__(
        self,
        *,
        profile_dir: str | None = None,
        state_file: str | None = None,
        headless: bool = False,
        search_url: str = DEFAULT_SEARCH_URL,
        page_load_timeout_ms: int = DEFAULT_PAGE_LOAD_TIMEOUT_MS,
        result_timeout_ms: int = DEFAULT_RESULT_TIMEOUT_MS,
    ) -> None:
        self._profile_dir = profile_dir
        self._state_file = str(state_file).strip() if state_file else ''
        self._headless = headless
        self._search_url = search_url
        self._page_load_timeout_ms = page_load_timeout_ms
        self._result_timeout_ms = result_timeout_ms
        self._consecutive_failures = 0
        self._playwright = None
        self._context = None
        self._browser = None

    @property
    def uses_state_file(self) -> bool:
        return bool(self._state_file) and not self._profile_dir

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    async def search(
        self,
        watch_item: WatchItem,
        *,
        limit: int = 10,
        snapshot_dir: str | Path | None = None,
    ) -> WameijiSearchOutcome:
        """真实读取一次 Wameiji 搜索结果。

        失败保护：
        - 同一个 _attempt 内部 action 最多重试 MAX_RETRIES_PER_ACTION 次
        - 整个 search 调用连续 MAX_CONSECUTIVE_FAILURES 次失败 → human_required
        """
        if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            return WameijiSearchOutcome(
                status="human_required",
                error_type="too_many_failures",
                error_message=(
                    f"Already failed {self._consecutive_failures} times in a row. "
                    "Refusing to keep hammering Wameiji. Inspect recent snapshots, "
                    "verify login state, then call reset_failure_count()."
                ),
                search_entry_url=self._search_entry_url(watch_item),
            )

        catalog = watch_item.catalog_no
        url = self._search_entry_url(watch_item)

        try:
            html, screenshot_path = await self._attempt(
                url=url, catalog=catalog, snapshot_dir=snapshot_dir,
            )
        except _StopAndHuman as stop:
            self._consecutive_failures += 1
            return WameijiSearchOutcome(
                status="human_required",
                error_type=stop.error_type,
                error_message=str(stop),
                search_entry_url=url,
                screenshot_path=stop.screenshot,
            )
        except _TransientError as transient:
            self._consecutive_failures += 1
            log.warning("Wameiji transient error (count=%d): %s",
                        self._consecutive_failures, transient)
            return WameijiSearchOutcome(
                status="human_required" if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES else "not_configured",
                error_type="transient_error",
                error_message=str(transient),
                search_entry_url=url,
            )
        except Exception as exc:
            self._consecutive_failures += 1
            log.exception("Wameiji unexpected error (count=%d)", self._consecutive_failures)
            return WameijiSearchOutcome(
                status="human_required" if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES else "not_configured",
                error_type="runner_error",
                error_message=f"{type(exc).__name__}: {exc}",
                search_entry_url=url,
            )

        # 成功：解析 + snapshot
        self._consecutive_failures = 0
        items = self._parse_with_local_parser(html, watch_item)
        snapshot_path: Path | None = None
        raw_snapshot_path: Path | None = None
        if snapshot_dir is not None:
            try:
                raw_snapshot_path = _save_raw_html_snapshot(
                    snapshot_dir, watch_item, html,
                )
                if screenshot_path is not None:
                    snapshot_path = screenshot_path
            except Exception as exc:
                log.warning("snapshot save failed: %s", exc)

        return WameijiSearchOutcome(
            items=items[:limit],
            raw_html=html,
            screenshot_path=snapshot_path,
            raw_snapshot_path=raw_snapshot_path,
            status="ok",
            search_entry_url=url,
        )

    def reset_failure_count(self) -> None:
        """人工确认后清零失败计数（CLI/Web 可调用）。"""
        self._consecutive_failures = 0

    async def close(self) -> None:
        """清理 Playwright 资源。"""
        try:
            if self._context is not None:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception:
            pass
        self._context = None
        self._browser = None
        self._playwright = None

    # ---- 内部 --------------------------------------------------------
    def _search_entry_url(self, watch_item: WatchItem) -> str:
        from urllib.parse import quote_plus
        return f"{self._search_url}?keywords={quote_plus(watch_item.catalog_no)}"

    def _parse_with_local_parser(self, html: str, watch_item: WatchItem) -> list[MarketItem]:
        """复用本模块顶部的 _WameijiCardParser / _extract_generic_items。

        这两个解析器在 parse_search_html() 里已经在用；
        真实浏览器拿到的 HTML 走同样的解析路径，保证 mock 与 live 一致。
        """
        if _requires_human(html):
            raise _StopAndHuman(
                error_type="security_check",
                message="captcha / security check / login challenge detected in HTML",
            )
        parser = _WameijiCardParser()
        parser.feed(html)
        if parser.items:
            return list(parser.items)
        return _extract_generic_items(html, watch_item)
    async def _attempt(
        self,
        *,
        url: str,
        catalog: str,
        snapshot_dir: str | Path | None,
    ) -> tuple[str, Path | None]:
        """单次尝试：开 context → 导航 → 等结果 → 读 HTML + 截图。

        内部对每个 Playwright action 最多重试 MAX_RETRIES_PER_ACTION 次；
        重试用尽仍失败抛 _TransientError（外层计数 +1）；
        命中验证码 / 登录失效抛 _StopAndHuman（外层立刻 human_required）。
        """
        ok, reason = _try_import_playwright()
        if not ok:
            raise _TransientError(f"playwright not installed: {reason}")

        from playwright.async_api import async_playwright, TimeoutError as PWTimeout
        from playwright._impl._errors import Error as PWError

        async with async_playwright() as p:
            self._playwright = p
            launch_kwargs: dict[str, Any] = {"headless": self._headless}

            if self._profile_dir:
                # Path A: persistent context loads a real Chrome profile (spec 1.2)
                launch_kwargs["user_data_dir"] = str(self._profile_dir)
                try:
                    self._context = await self._retry_async(
                        lambda: p.chromium.launch_persistent_context(**launch_kwargs),
                        op_name="launch_browser",
                    )
                except PWError as exc:
                    raise _TransientError(f"launch failed: {exc}") from exc
                self._browser = None
            else:
                # Path B: load Playwright storage_state from wameiji_state.json
                storage_state = self._load_storage_state()
                self._browser = await self._retry_async(
                    lambda: p.chromium.launch(**launch_kwargs),
                    op_name="launch_browser",
                )
                context_kwargs: dict[str, Any] = {}
                if storage_state is not None:
                    context_kwargs["storage_state"] = storage_state
                try:
                    self._context = await self._retry_async(
                        lambda: self._browser.new_context(**context_kwargs),
                        op_name="new_context",
                    )
                except PWError as exc:
                    raise _TransientError(f"new_context failed: {exc}") from exc
            try:
                page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            except Exception as exc:
                raise _TransientError(f"open page failed: {exc}") from exc

            # 人类节奏
            await asyncio.sleep(random.uniform(0.5, 1.5))

            # 1) 导航到搜索页
            try:
                await self._retry_async(
                    lambda: page.goto(url, timeout=self._page_load_timeout_ms, wait_until="domcontentloaded"),
                    op_name="goto_search",
                )
            except PWTimeout as exc:
                raise _TransientError(f"goto timeout: {exc}") from exc
            except PWError as exc:
                msg = str(exc).lower()
                if any(token in msg for token in ["captcha", "cloudflare", "forbidden", "security"]):
                    shot = await _safe_screenshot(page, snapshot_dir, catalog)
                    raise _StopAndHuman("security_check", f"goto blocked: {exc}", shot) from exc
                raise _TransientError(f"goto failed: {exc}") from exc

            # 2) 等待结果出现
            try:
                await self._retry_async(
                    lambda: page.wait_for_selector(
                        "[data-item-card], .goods-item, .goods-list, .item-card, .product-card, .search-result",
                        timeout=self._result_timeout_ms,
                    ),
                    op_name="wait_for_results",
                )
            except PWTimeout:
                # 没有结果不一定就是错——可能是空结果页
                log.info("Wameiji search returned no result-card selectors within %dms",
                         self._result_timeout_ms)
            except PWError as exc:
                raise _TransientError(f"wait_for_results failed: {exc}") from exc

            # 3) 截图
            screenshot_path = await _safe_screenshot(page, snapshot_dir, catalog)

            # 4) 读 HTML
            html = await page.content()

            # 5) 再做一次安全检查（页面文本 + DOM）
            if _requires_human(html):
                raise _StopAndHuman(
                    "security_check",
                    "captcha / security check / login challenge detected after navigation",
                    screenshot_path,
                )

            return html, screenshot_path

    async def _retry_async(self, op_factory, *, op_name: str):
        """同一个 action 重试最多 MAX_RETRIES_PER_ACTION 次。"""
        last_exc: Exception | None = None
        for attempt in range(1, self.MAX_RETRIES_PER_ACTION + 1):
            try:
                return await op_factory()
            except Exception as exc:
                last_exc = exc
                log.warning("Wameiji op %s attempt %d/%d failed: %s",
                            op_name, attempt, self.MAX_RETRIES_PER_ACTION, exc)
                if attempt < self.MAX_RETRIES_PER_ACTION:
                    await asyncio.sleep(random.uniform(1.0, 2.5))
        assert last_exc is not None
        raise last_exc


    def _load_storage_state(self):
        """Load wameiji_state.json and return its playwright_storage_state.

        Returns None when state_file is unset or invalid; the runner still
        launches the browser without cookies, which surfaces as a login wall.
        """
        if not self._state_file:
            return None
        try:
            from cd_monitor.services.wameiji_login_state import (
                load_wameiji_login_state,
                WameijiLoginStateError,
            )
        except Exception as exc:
            log.warning('wameiji_login_state import failed: %s', exc)
            return None
        try:
            return load_wameiji_login_state(self._state_file)
        except WameijiLoginStateError as exc:
            log.warning('wameiji state_file invalid (%s): %s', self._state_file, exc)
            return None

# ---- 内部异常 ---------------------------------------------------------
class _StopAndHuman(Exception):
    """命中验证码/安全验证/登录失效——立即停止，返回 human_required。"""
    def __init__(self, error_type: str, message: str, screenshot: Path | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.screenshot = screenshot


class _TransientError(Exception):
    """临时错误（网络超时、Playwright 临时故障）——累加失败计数。"""


# ---- 内部辅助 ---------------------------------------------------------
async def _safe_screenshot(
    page: Any,
    snapshot_dir: str | Path | None,
    catalog: str,
) -> Path | None:
    if snapshot_dir is None:
        return None
    try:
        out = _snapshot_dir(snapshot_dir) / f"wameiji_{catalog}_latest.png"
        await page.screenshot(path=str(out), full_page=True)
        return out
    except Exception as exc:
        log.warning("screenshot failed: %s", exc)
        return None


def _snapshot_dir(snapshot_dir: str | Path) -> Path:
    p = Path(snapshot_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _save_raw_html_snapshot(
    snapshot_dir: str | Path,
    watch_item: WatchItem,
    html: str,
) -> Path:
    """把 raw HTML 也存到 snapshot_dir（除了 save_snapshot 写的 JSON）。"""
    p = _snapshot_dir(snapshot_dir) / f"wameiji_{watch_item.catalog_no}_latest.html"
    p.write_text(html, encoding="utf-8")
    return p


# ---- 适配器扩展 -------------------------------------------------------
class WameijiBrowserAdapterWithRunner(WameijiBrowserAdapter):
    """WameijiBrowserAdapter + Playwright runner 扩展。

    与旧 WameijiBrowserAdapter 区别：search_async() 真正调起 Playwright。
    老的 search_status() / parse_search_html() 行为完全保留。
    """

    def __init__(
        self,
        enabled: bool = False,
        profile_dir: str | None = None,
        state_file: str | None = None,
        headless: bool = False,
        snapshot_dir: str | Path | None = None,
        search_url: str = DEFAULT_SEARCH_URL,
    ) -> None:
        super().__init__(enabled=enabled)
        self._profile_dir = profile_dir
        self._state_file = str(state_file).strip() if state_file else ''
        self._headless = headless
        self._snapshot_dir = Path(snapshot_dir) if snapshot_dir else None
        self._search_url = search_url
        self._runner: WameijiPlaywrightRunner | None = None

    @property
    def runner(self) -> WameijiPlaywrightRunner | None:
        return self._runner

    @property
    def uses_state_file(self) -> bool:
        return bool(self._state_file) and not self._profile_dir

    def ensure_runner(self) -> WameijiPlaywrightRunner:
        if self._runner is None:
            self._runner = WameijiPlaywrightRunner(
                profile_dir=self._profile_dir,
                state_file=self._state_file or None,
                headless=self._headless,
                search_url=self._search_url,
            )
        return self._runner

    async def aclose(self) -> None:
        if self._runner is not None:
            await self._runner.close()
            self._runner = None

    def search_status(self, watch_item):
        from urllib.parse import quote_plus
        if not self.enabled:
            return AdapterStatus(
                status="disabled",
                error_type="browser_disabled",
                error_message="browser.enabled=false; real Wameiji browser reads are not run.",
            )
        if not self._profile_dir and not self._state_file:
            return super().search_status(watch_item)
        if self._state_file and not self._profile_dir:
            from cd_monitor.services.wameiji_login_state import inspect_wameiji_login_state
            insp = inspect_wameiji_login_state(self._state_file)
            if insp["status"] != "ready":
                return AdapterStatus(
                    status="human_required",
                    error_type=insp.get("error_type") or "state_file_invalid",
                    error_message=insp.get("error_message") or "wameiji state file invalid",
                    search_entry_url=f"{self._search_url}?keywords={quote_plus(watch_item.catalog_no)}",
                    state_file_status=insp["status"],
                    state_file_path=self._state_file,
                    state_cookie_domains=insp.get("cookie_domains") or [],
                    capture_instruction=(
                        "Use the Wameiji Chrome extension to capture a fresh login state, "
                        "then re-import via web UI panel or cli wameiji-login-state-import."
                    ),
                )
            return AdapterStatus(
                status="human_required",
                error_type="async_capture_required",
                error_message=(
                    "Wameiji login_state file is ready; live reads must go through the async "
                    "capture path (cli scan-live-html / capture-live-html --source wameiji). "
                    "sync search_status does not start a browser."
                ),
                search_entry_url=f"{self._search_url}?keywords={quote_plus(watch_item.catalog_no)}",
                login_state_ready=True,
                state_file_status="ready",
                state_file_path=self._state_file,
                state_cookie_domains=insp.get("cookie_domains") or [],
                capture_instruction=(
                    "Run: python -m cd_monitor.cli capture-live-html --source wameiji "
                    f"--catalog-no <CATALOG> --output <HTML> --state-file {self._state_file}"
                ),
            )
        return AdapterStatus(
            status="human_required",
            error_type="async_capture_required",
            error_message=(
                "Wameiji profile is configured but live reads must go through the async "
                "capture path (cli scan-live-html / capture-live-html --source wameiji). "
                "sync search_status does not start a browser."
            ),
            search_entry_url=f"{self._search_url}?keywords={quote_plus(watch_item.catalog_no)}",
            capture_instruction=(
                "Run: python -m cd_monitor.cli capture-live-html --source wameiji "
                "--catalog-no <CATALOG> --output <HTML> --profile-dir <CHROME_USER_DATA_DIR>"
            ),
        )

    async def search_async(
        self,
        watch_item: WatchItem,
        *,
        limit: int = 10,
    ) -> AdapterStatus:
        """真实异步读取；返回完整 AdapterStatus（含 snapshot 路径）。"""
        from urllib.parse import quote_plus
        if not self.enabled:
            return AdapterStatus(
                status="disabled",
                error_type="browser_disabled",
                error_message="browser.enabled=false; real Wameiji browser reads are not run.",
            )
        if not self._profile_dir and not self._state_file:
            return AdapterStatus(
                status="not_configured",
                error_type="no_profile_or_state",
                error_message=(
                    "Set browser.wameiji_profile_dir in config or "
                    "WAMEIJI_PROFILE_DIR env to a real Chrome user-data-dir."
                ),
                search_entry_url=f"{self._search_url}?keyword={quote_plus(watch_item.catalog_no)}",
            )
        runner = self.ensure_runner()
        outcome = await runner.search(
            watch_item,
            limit=limit,
            snapshot_dir=self._snapshot_dir,
        )
        if outcome.status == "ok" and self._snapshot_dir is not None:
            try:
                payload = {
                    "source": "wameiji",
                    "catalog_no": watch_item.catalog_no,
                    "search_entry_url": outcome.search_entry_url,
                    "items": [
                        {
                            "title": it.title,
                            "price": it.price,
                            "currency": it.currency,
                            "source_site": it.source_site,
                            "url": it.url,
                            "image_url": it.image_url,
                            "availability": it.availability,
                            "raw_text": it.raw_text,
                        }
                        for it in outcome.items
                    ],
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
                save_snapshot(self._snapshot_dir, "wameiji_live", payload)
            except Exception as exc:
                log.warning("save_snapshot failed: %s", exc)
        return AdapterStatus(
            status=outcome.status,
            items=outcome.items,
            error_type=outcome.error_type,
            error_message=outcome.error_message,
            screenshot_path=str(outcome.screenshot_path) if outcome.screenshot_path else None,
            raw_snapshot_path=str(outcome.raw_snapshot_path) if outcome.raw_snapshot_path else None,
            search_entry_url=outcome.search_entry_url,
        )
