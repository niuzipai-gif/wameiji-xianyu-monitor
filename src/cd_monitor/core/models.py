from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WatchItem:
    catalog_no: str
    jan: str | None = None
    artist: str | None = None
    title_jp: str | None = None
    title_cn: str | None = None
    edition: str | None = None
    required_keywords: list[str] = field(default_factory=list)
    excluded_keywords: list[str] = field(default_factory=list)
    match_mode: str = "any"  # "any" (OR) | "all" (AND) keyword match
    priority: int = 1
    expected_holding_days: int = 30
    min_margin: float = 0.30
    min_diff: float = 1500.0
    notify_channel: str = "none"
    platform: str = "both"
    enabled: bool = True
    # P5.3: task-level account binding (port from Usagi ai-goofish-monitor).
    # Mirrors Task.account_state_file + Task.account_strategy pair:
    #   strategy == "fixed" requires account_state_file to be set.
    account_state_file: str | None = None
    account_strategy: str = "auto"

    # P5.4: AI/keyword mode + per-task AI prompt (mirrors Usagi
    # domain/models/task.py Task + TaskGenerateRequest fields).
    # decision_mode: "ai" (default, lets LLM score the candidate) or "keyword"
    #   (LLM is skipped; the configured required/excluded keywords drive the
    #   match confidence and final decision entirely).
    # description: free-text requirement handed to the AI prompt composer.
    #   Required when decision_mode == "ai".
    # ai_prompt_base_file / ai_prompt_criteria_file: optional overrides for the
    #   default base prompt template + per-task criteria snippet. None falls
    #   back to prompts/base_prompt.txt at runtime.
    decision_mode: str = "ai"
    description: str | None = None
    ai_prompt_base_file: str | None = None
    ai_prompt_criteria_file: str | None = None

    # P5.5 filter columns (mirrors Usagi ``tasks`` filter set).
    # region: free-text locale tag the Web UI filters on.
    # personal_only: boolean whether to restrict to personal listings.
    # analyze_images: whether to send images to the AI scorer.
    # min_price / max_price: optional CNY bounds surfaced on the task edit form.
    # max_pages: page depth cap when scanning.
    region: str | None = None
    personal_only: bool = False
    analyze_images: bool = True
    min_price: float | None = None
    max_price: float | None = None
    max_pages: int = 5

    def __post_init__(self) -> None:
        # Lazy import to avoid pulling services into core.
        from cd_monitor.services.account_strategy import (
            assert_strategy_consistent,
            normalize_account_strategy,
        )
        # Normalize strategy so empty/unknown strings collapse to canonical
        # values; reject fixed-without-file early.
        self.account_strategy = normalize_account_strategy(
            self.account_strategy,
            self.account_state_file,
        )
        assert_strategy_consistent(self.account_strategy, self.account_state_file)
        # Normalize decision_mode to a known value. Note: the "ai mode
        # requires description" invariant is enforced at the API boundary
        # (see cd_monitor.web_server._validate_decision_mode_update /
        # POST /api/watchlist), not here, so reading legacy rows without
        # description does not blow up. Mirrors Usagi where TaskCreate /
        # TaskGenerateRequest validate the invariant but Task (the storage
        # shape) does not.
        mode = str(self.decision_mode or "").strip().lower()
        if mode not in {"ai", "keyword"}:
            mode = "ai"
        self.decision_mode = mode


@dataclass(slots=True)
class MarketItem:
    source: str
    title: str
    price: float
    currency: str = "JPY"
    source_site: str | None = None
    external_item_id: str | None = None
    catalog_no: str | None = None
    jan: str | None = None
    price_cny_display: float | None = None
    url: str | None = None
    image_url: str | None = None
    availability: str = "unknown_but_visible"
    condition_text: str | None = None
    fees_hint: str | None = None
    raw_text: str | None = None
    screenshot_path: str | None = None
    # Search cards are discovery hints only.  Automatic comparison is allowed
    # only after the item was read from its own Wameiji detail page.
    detail_verified: bool = False
    # Detail-page fees are optional because search cards generally omit them.
    # When present, the landed-cost model must use them instead of defaults.
    japan_domestic_shipping_jpy: float | None = None
    proxy_fee_jpy: float | None = None


@dataclass(slots=True)
class XianyuPriceSample:
    catalog_no: str
    title: str
    price_cny: float
    url: str | None = None
    image_url: str | None = None
    image_phash: str | None = None  # perceptual hash for visual match against wameiji item
    image_dhash: str | None = None
    cover_text: str | None = None  # OCR text from cover image (for cover_jaccard)
    seller_text: str | None = None
    raw_text: str | None = None
    is_valid: bool = True
    invalid_reason: str | None = None
    market_item_id: int | None = None  # id into market_items (for feeding display)


@dataclass(slots=True)
class XianyuPriceEstimate:
    reference_price_cny: float
    valid_sample_count: int
    liquidity_status: str
    expected_sale_price_cny: float = 0.0
    valid_samples: list[XianyuPriceSample] = field(default_factory=list)
    invalid_samples: list[XianyuPriceSample] = field(default_factory=list)


@dataclass(slots=True)
class MatchResult:
    confidence: float
    matched_identifiers: list[str] = field(default_factory=list)
    positive_reasons: list[str] = field(default_factory=list)
    negative_reasons: list[str] = field(default_factory=list)
    fatal_flags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CostConfig:
    wameiji_exchange_rate: float = 0.046
    default_proxy_fee_jpy: float = 200
    default_japan_domestic_shipping_jpy: float = 0
    add_on_fee_jpy: float = 0
    merge_fee_jpy: float = 0
    international_shipping_per_cd_cny: float = 18
    duty_cny: float = 0
    optional_inspection_cny: float = 0
    optional_reinforcement_cny: float = 0
    other_related_fee_cny: float = 0
    china_reship_cost_cny: float = 12
    packaging_cost_cny: float = 2
    after_sale_reserve_cny: float = 5
    risk_reserve_min_cny: float = 8
    risk_reserve_rate: float = 0.03
    annual_capital_rate: float = 0.08


@dataclass(slots=True)
class CostBreakdown:
    first_payment_cny: float
    estimated_second_payment_cny: float
    china_reship_cost_cny: float
    risk_reserve_cny: float
    capital_cost_cny: float
    total_landed_cost_cny: float


@dataclass(slots=True)
class EvaluationConfig:
    min_valid_price_cny: float = 10
    max_valid_price_cny: float = 2000
    sample_limit: int = 15
    negotiation_discount: float = 0.92
    liquidity_discount_default: float = 0.90
    xianyu_fee_rate: float = 0.006
    xianyu_fee_cap_cny: float = 60
    domestic_outbound_shipping_cny: float = 8
    packaging_cost_cny: float = 2
    after_sale_reserve_cny: float = 5
    strong_profit_min_cny: float = 35
    strong_margin_min: float = 0.35
    weak_profit_min_cny: float = 50
    weak_margin_min: float = 0.25
    min_match_confidence_strong: float = 0.85
    min_match_confidence_weak: float = 0.75
    min_xianyu_samples_strong: int = 3
    min_xianyu_samples_weak: int = 2


@dataclass(slots=True)
class Opportunity:
    catalog_no: str
    item: MarketItem
    xianyu_reference_price: float
    expected_sale_price: float
    landed_cost: float
    expected_revenue: float
    expected_profit: float
    net_margin: float
    turnover_adjusted_roi: float
    match_confidence: float
    valid_xianyu_sample_count: int
    liquidity_status: str
    decision: str
    risk_labels: list[str] = field(default_factory=list)
    opportunity_hash: str | None = None
    review_advice: str = "人工复核后再决定是否采购"


@dataclass(slots=True)
class AdapterStatus:
    status: str
    items: list[Any] = field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
    screenshot_path: str | None = None
    raw_snapshot_path: str | None = None
    search_entry_url: str | None = None
    capture_instruction: str | None = None
    login_state_ready: bool = False
    state_file_status: str | None = None
    state_file_path: str | None = None
    state_cookie_domains: list[str] = field(default_factory=list)
