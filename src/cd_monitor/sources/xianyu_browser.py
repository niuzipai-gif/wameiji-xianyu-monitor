from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote_plus

from cd_monitor.core.identifiers import (
    extract_catalog_candidates,
    extract_jan_candidates,
    normalize_catalog_no_compact,
)
from cd_monitor.core.models import AdapterStatus, WatchItem, XianyuPriceSample
from cd_monitor.core.title_query import matches_title_search_query
from cd_monitor.sources.base import BrowserHarnessAdapter


class XianyuBrowserAdapter(BrowserHarnessAdapter):

    def __init__(self, enabled: bool = False, state_file: str | Path | None = None) -> None:
        super().__init__(enabled=enabled)
        self.state_file = str(state_file or "").strip()

    def search_status(self, watch_item: WatchItem) -> AdapterStatus:
        search_entry_url = f"https://www.goofish.com/search?q={quote_plus(watch_item.catalog_no)}"
        capture_instruction = (
            "Open Xianyu search in a logged-in visible browser, search the catalog/JAN, "
            "then provide the visible search result HTML through Evaluate Pasted HTML or evaluate-html."
        )
        if not self.enabled:
            return AdapterStatus(
                status="disabled",
                error_type="browser_disabled",
                error_message="browser.enabled=false; real Xianyu browser reads are not run.",
            )
        state = _inspect_storage_state(self.state_file)
        if self.state_file and state.status != "ready":
            return AdapterStatus(
                status="human_required",
                error_type=state.error_type,
                error_message=state.error_message,
                search_entry_url=search_entry_url,
                capture_instruction=capture_instruction,
                state_file_status=state.status,
                state_file_path=self.state_file,
                state_cookie_domains=state.cookie_domains,
            )
        if state.status == "ready":
            return AdapterStatus(
                status="human_required",
                error_type="executor_missing",
                error_message=(
                    "Xianyu Playwright storage_state ready, but no visible browser executor is wired yet. "
                    "Use manual visible HTML import until the executor is explicitly configured."
                ),
                search_entry_url=search_entry_url,
                capture_instruction=capture_instruction,
                login_state_ready=True,
                state_file_status="ready",
                state_file_path=self.state_file,
                state_cookie_domains=state.cookie_domains,
            )
        return AdapterStatus(
            status="human_required",
            error_type="not_configured",
            error_message=(
                "Configure a logged-in visible browser profile before reading Xianyu samples. "
                "Only search-result cards may be read; no publishing, messaging, or buying."
            ),
            search_entry_url=search_entry_url,
            capture_instruction=capture_instruction,
        )

    def parse_search_html(self, html: str, watch_item: WatchItem) -> AdapterStatus:
        if _requires_human(html):
            return AdapterStatus(
                status="human_required",
                error_type="security_check",
                error_message="Captcha, security check, or login challenge detected.",
            )
        parser = _XianyuCardParser(watch_item.catalog_no)
        parser.feed(html)
        had_card_items = bool(parser.items)
        if had_card_items:
            parser.items = [
                item
                for item in parser.items
                if _matches_search_query(item.title, watch_item.catalog_no)
            ]
        if not had_card_items and not parser.items:
            parser.items = _extract_generic_samples(html, watch_item)
        return AdapterStatus(status="ok", items=parser.items)


class _XianyuCardParser(HTMLParser):
    _VOID_TAGS = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )

    def __init__(self, catalog_no: str) -> None:
        super().__init__()
        self.catalog_no = catalog_no
        self.items: list[XianyuPriceSample] = []
        self._in_card = False
        self._card_depth = 0
        self._title_depth: int | None = None
        self._title_attr_depth: int | None = None
        self._price_depth: int | None = None
        self._price_region_depth: int | None = None
        self._current: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = (attr.get("class") or "").split()
        started_card = "data-xianyu-card" in attr or _has_class_prefix(classes, "feeds-item-wrap-")
        if started_card:
            # The current Goofish result surface uses an <a> as the card root,
            # while older saved HTML used a data-xianyu-card <div>. Both are
            # intentionally handled by the same bounded card parser.
            self._in_card = True
            self._card_depth = 1
            self._current = {}
            self._title_depth = None
            self._title_attr_depth = None
            self._price_depth = None
            self._price_region_depth = None
            if attr.get("href"):
                self._current["url"] = attr["href"] or ""
            if tag == "img":
                image_url = _first_attr_text(attr, "src", "data-src", "data-original")
                if image_url:
                    self._current["image_url"] = image_url
            return
        if not self._in_card:
            return
        if tag not in self._VOID_TAGS:
            self._card_depth += 1
        if tag == "a" and attr.get("href") and "url" not in self._current:
            self._current["url"] = attr["href"] or ""
        if tag == "img":
            image_url = _first_attr_text(attr, "src", "data-src", "data-original")
            if image_url:
                self._current["image_url"] = image_url

        if _has_class_prefix(classes, "row1-wrap-title-"):
            self._title_depth = self._card_depth
            title_attr = _first_attr_text(attr, "title", "aria-label")
            if title_attr:
                self._current["title"] = title_attr
                self._title_attr_depth = self._card_depth
        elif tag == "a" and self._title_depth is None:
            # Compatibility path for the original data-xianyu-card fixture.
            self._title_depth = self._card_depth

        if "data-price" in attr:
            self._current["price"] = attr.get("data-price") or ""
            self._price_depth = self._card_depth
        elif _has_class_prefix(classes, "number-") or _has_class_prefix(classes, "decimal-"):
            self._price_depth = self._card_depth
        elif _has_class_prefix(classes, "row3-wrap-price-") or _has_class_prefix(classes, "price-wrap-"):
            # Fallback for a future class rename where the number spans are
            # removed but the price container remains visible.
            self._price_region_depth = self._card_depth

    def handle_data(self, data: str) -> None:
        if not self._in_card or not data.strip():
            return
        value = data.strip()
        if self._title_depth is not None and self._card_depth >= self._title_depth:
            if self._title_attr_depth is None:
                self._current["title"] = (self._current.get("title", "") + value).strip()
        if self._price_depth is not None and self._card_depth >= self._price_depth:
            self._current["price"] = (self._current.get("price", "") + value).strip()
        elif self._price_region_depth is not None and self._card_depth >= self._price_region_depth:
            self._current["price_region"] = (
                self._current.get("price_region", "") + value
            ).strip()

    def handle_endtag(self, tag: str) -> None:
        if not self._in_card or tag in self._VOID_TAGS:
            return
        if self._title_attr_depth == self._card_depth:
            self._title_attr_depth = None
        if self._title_depth == self._card_depth:
            self._title_depth = None
        if self._price_depth == self._card_depth:
            self._price_depth = None
        if self._price_region_depth == self._card_depth:
            self._price_region_depth = None
        self._card_depth -= 1
        if self._card_depth > 0:
            return
        title = self._current.get("title", "").strip()
        price_text = self._current.get("price", "") or self._current.get("price_region", "")
        price = _parse_price(price_text)
        if title and price > 0:
            self.items.append(
                XianyuPriceSample(
                    catalog_no=self.catalog_no,
                    title=title,
                    price_cny=price,
                    url=self._current.get("url"),
                    image_url=self._current.get("image_url"),
                    raw_text=" ".join(self._current.values()),
                )
            )
        self._in_card = False
        self._card_depth = 0
        self._title_depth = None
        self._title_attr_depth = None
        self._price_depth = None
        self._price_region_depth = None
        self._current = {}

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)


def _has_class_prefix(classes: list[str], prefix: str) -> bool:
    return any(value.startswith(prefix) for value in classes)


def _parse_price(value: str) -> float:
    digits = re.sub(r"[^\d.]", "", value)
    return float(digits) if digits else 0.0


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
    return matches_title_search_query(text, query)


def _extract_generic_samples(html: str, watch_item: WatchItem) -> list[XianyuPriceSample]:
    parser = _GenericBlockParser()
    parser.feed(html)
    query = watch_item.catalog_no
    catalog = normalize_catalog_no_compact(query)
    precise_query = _is_precise_identifier_query(query)
    samples: list[XianyuPriceSample] = []
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
        samples.append(
            XianyuPriceSample(
                catalog_no=watch_item.catalog_no,
                title=title,
                price_cny=price,
                url=url,
                image_url=block["images"][0] if block["images"] else None,
                raw_text=text,
            )
        )
    return samples


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


@dataclass(frozen=True, slots=True)
class _StorageStateInspection:
    status: str
    error_type: str | None = None
    error_message: str | None = None
    cookie_domains: list[str] | None = None


_XIANYU_AUTH_DOMAINS = ("goofish.com", "xianyu", "taobao.com", "alibaba.com", "tmall.com")


def _inspect_storage_state(state_file: str) -> _StorageStateInspection:
    if not state_file:
        return _StorageStateInspection(status="not_configured", cookie_domains=[])
    path = Path(state_file)
    if not path.exists() or not path.is_file():
        return _StorageStateInspection(
            status="missing",
            error_type="state_file_missing",
            error_message="Configured Xianyu storage_state file does not exist.",
            cookie_domains=[],
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _StorageStateInspection(
            status="invalid",
            error_type="invalid_state_file",
            error_message="Configured Xianyu storage_state file is not valid JSON.",
            cookie_domains=[],
        )
    if not isinstance(data, dict):
        return _StorageStateInspection(
            status="invalid",
            error_type="invalid_state_file",
            error_message="Configured Xianyu storage_state file must be a JSON object.",
            cookie_domains=[],
        )
    cookies = data.get("cookies")
    if not isinstance(cookies, list) or not cookies:
        return _StorageStateInspection(
            status="invalid",
            error_type="invalid_state_file",
            error_message="Configured Xianyu storage_state file must include a non-empty cookies list.",
            cookie_domains=[],
        )
    domains = sorted(
        {
            str(cookie.get("domain", "")).strip()
            for cookie in cookies
            if isinstance(cookie, dict) and str(cookie.get("domain", "")).strip()
        }
    )
    if not domains:
        return _StorageStateInspection(
            status="invalid",
            error_type="invalid_state_file",
            error_message="Configured Xianyu storage_state cookies are missing domains.",
            cookie_domains=[],
        )
    if not any(_is_xianyu_auth_domain(domain) for domain in domains):
        return _StorageStateInspection(
            status="invalid",
            error_type="invalid_state_file",
            error_message="Configured Xianyu storage_state file does not contain Goofish/Xianyu auth domains.",
            cookie_domains=domains,
        )
    return _StorageStateInspection(status="ready", cookie_domains=domains)


def _is_xianyu_auth_domain(domain: str) -> bool:
    lowered = domain.lower()
    return any(token in lowered for token in _XIANYU_AUTH_DOMAINS)


_HARD_SECURITY_MARKERS = (
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
    # Result cards contain full seller descriptions. Generic phrases such as
    # “验证码” or “风控” are ordinary listing text, so only accept the softer
    # signals when the document itself identifies as a challenge page.
    title_match = _HTML_TITLE_RE.search(html)
    if title_match is None:
        return False
    title = re.sub(r"<[^>]+>", " ", title_match.group(1)).casefold()
    return any(token in title for token in _TITLE_SECURITY_MARKERS)
