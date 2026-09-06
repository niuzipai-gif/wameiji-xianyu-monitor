from __future__ import annotations

from abc import ABC, abstractmethod

from cd_monitor.core.models import AdapterStatus, MarketItem, WatchItem, XianyuPriceSample


class WameijiSourceAdapter(ABC):
    @abstractmethod
    def search(self, watch_item: WatchItem, limit: int = 10) -> list[MarketItem]:
        raise NotImplementedError


class XianyuSourceAdapter(ABC):
    @abstractmethod
    def search_samples(self, watch_item: WatchItem, limit: int = 15) -> list[XianyuPriceSample]:
        raise NotImplementedError


class BrowserHarnessAdapter(ABC):
    # Default safety boundary (spec §1.1). Concrete adapters may override per-source.
    prohibited_actions: frozenset[str] = frozenset({
        "auto_order",
        "auto_pay",
        "auto_message",
        "auto_publish",
        "add_to_cart",
        "message_seller",
        "contact_seller",
        "place_bid",
        "captcha_bypass",
        "cloudflare_bypass",
        "multi_account_evasion",
    })

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    @abstractmethod
    def search_status(self, watch_item: WatchItem) -> AdapterStatus:
        raise NotImplementedError
