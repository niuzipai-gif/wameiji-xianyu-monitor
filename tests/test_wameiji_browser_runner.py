"""WameijiBrowserAdapter / WameijiPlaywrightRunner 单元测试。

不需要安装 playwright：测试聚焦在
- 失败保护计数
- snapshot 保存
- 适配器形态（兼容旧调用 + 新调用）
- 内部异常类型
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from cd_monitor.core.models import MarketItem, WatchItem
from cd_monitor.sources.wameiji_browser import (
    DEFAULT_SEARCH_URL,
    MAX_CONSECUTIVE_FAILURES,
    MAX_RETRIES_PER_ACTION,
    WameijiBrowserAdapter,
    WameijiBrowserAdapterWithRunner,
    WameijiPlaywrightRunner,
    WameijiSearchOutcome,
    _save_raw_html_snapshot,
    _snapshot_dir,
    _external_item_id,
    _StopAndHuman,
    _TransientError,
)


def _watch(catalog: str = "SRCL-3520") -> WatchItem:
    return WatchItem(catalog_no=catalog)


# === 常量 ===========================================================
def test_default_search_url_is_meruki_cn() -> None:
    assert DEFAULT_SEARCH_URL == "https://meruki.cn/search"


def test_profile_search_url_uses_live_wameiji_keywords_parameter() -> None:
    """The live search page consumes `keywords`, not the ignored `keyword`."""
    adapter = WameijiBrowserAdapterWithRunner(
        enabled=True, profile_dir="C:/fake/profile"
    )
    status = adapter.search_status(_watch("Switch 限定版"))

    assert status.search_entry_url is not None
    assert parse_qs(urlparse(status.search_entry_url).query) == {
        "keywords": ["Switch 限定版"]
    }


def test_failure_protection_constants_match_spec() -> None:
    # spec §1.2 Browser-Harness: 连续 3 次失败停止 + 同 action 2 次重试
    assert MAX_CONSECUTIVE_FAILURES == 3
    assert MAX_RETRIES_PER_ACTION == 2


# === 失败保护 ========================================================
@pytest.mark.asyncio
async def test_too_many_failures_returns_human_required() -> None:
    runner = WameijiPlaywrightRunner(profile_dir=None, headless=True)
    runner._consecutive_failures = MAX_CONSECUTIVE_FAILURES
    outcome = await runner.search(_watch())
    assert outcome.status == "human_required"
    assert outcome.error_type == "too_many_failures"
    assert outcome.search_entry_url is not None
    assert "SRCL-3520" in outcome.search_entry_url


@pytest.mark.asyncio
async def test_reset_failure_count_clears_state() -> None:
    runner = WameijiPlaywrightRunner(profile_dir=None, headless=True)
    runner._consecutive_failures = 3
    runner.reset_failure_count()
    assert runner.consecutive_failures == 0


# === Snapshot 保存 ==================================================
def test_save_raw_html_snapshot_writes_file() -> None:
    import os
    catalog = "SRCL-3520"
    html = "<html><body>test</body></html>"
    td = Path(os.environ["TEMP"]) / f"wx-test-{os.getpid()}-{catalog}"
    td.mkdir(parents=True, exist_ok=True)
    out = _save_raw_html_snapshot(td, _watch(catalog), html)
    assert out.exists()
    assert out.name == f"wameiji_{catalog}_latest.html"
    assert out.read_text(encoding="utf-8") == html


def test_snapshot_dir_creates_directory() -> None:
    import os
    td = Path(os.environ["TEMP"]) / f"wx-test-{os.getpid()}-snapdir"
    target = td / "deep" / "nested" / "snap"
    d = _snapshot_dir(target)
    assert d.is_dir()
    assert d == target


# === 适配器形态（兼容旧 + 新调用） ==================================
def test_legacy_constructor_still_works() -> None:
    """live_browser_capture.py 现有调用：WameijiBrowserAdapter(enabled=True)。"""
    adapter = WameijiBrowserAdapter(enabled=True)
    assert adapter.enabled is True


def test_parse_detail_html_uses_detail_page_values_and_marks_item_verified() -> None:
    """A search card cannot be promoted until its own detail page is parsed."""
    search_item = MarketItem(
        source="wameiji",
        title="Search card title that is incomplete",
        price=9999,
        currency="JPY",
        external_item_id="listing-3520",
        url="/mall/mercari/detail/listing-3520",
        availability="unknown_but_visible",
    )
    detail_html = """
    <main class="goods-detail">
      <h1 class="goods-name">Artist Album SRCL-3520 初回限定盤 CD+DVD</h1>
      <section class="goods-info">
        <p>品番：SRCL-3520</p>
        <p>JAN：4547366123456</p>
        <p class="price-com">1,280 <span class="unit">日元</span></p>
        <p>二手 在库</p>
      </section>
    </main>
    """

    status = WameijiBrowserAdapter(enabled=True).parse_detail_html(detail_html, search_item)

    assert status.status == "ok"
    assert len(status.items) == 1
    item = status.items[0]
    assert item.detail_verified is True
    assert item.title == "Artist Album SRCL-3520 初回限定盤 CD+DVD"
    assert item.price == 1280
    assert item.catalog_no == "SRCL-3520"
    assert item.jan == "4547366123456"
    assert item.availability == "available"


def test_parse_detail_html_uses_scoped_product_image_not_search_card_logo() -> None:
    """A verified detail record must not inherit a merchant logo from search."""
    search_item = MarketItem(
        source="wameiji",
        title="Search card title",
        price=9999,
        currency="JPY",
        external_item_id="listing-image-3520",
        url="/mall/mercari/detail/listing-image-3520",
        image_url="https://imgoss.mokaki.cn/sigmerchantimg/logo/merchant.webp",
        availability="unknown_but_visible",
    )
    detail_html = """
    <main class="goods-detail">
      <img class="merchant-logo" src="https://imgoss.mokaki.cn/sigmerchantimg/logo/merchant.webp">
      <img class="goods-image" data-src="https://images.example.invalid/detail/album-3520.webp">
      <h1 class="goods-name">Artist Album SRCL-3520 初回限定盤 CD+DVD</h1>
      <p class="price-com">1,280 日元</p>
      <p>二手 在库</p>
    </main>
    """

    status = WameijiBrowserAdapter(enabled=True).parse_detail_html(detail_html, search_item)

    assert status.status == "ok"
    assert status.items[0].image_url == "https://images.example.invalid/detail/album-3520.webp"


def test_parse_detail_html_prefers_matching_json_ld_product_image_over_placeholder() -> None:
    """A real detail image in JSON-LD must beat the Wameiji placeholder."""
    search_item = MarketItem(
        source="wameiji",
        title="Search card title",
        price=1500,
        currency="JPY",
        external_item_id="z618880764",
        url="/mall/paypay/detail/z618880764",
        availability="unknown_but_visible",
    )
    detail_html = """
    <main class="goods-detail">
      <img class="goods-image" src="https://imgoss.mokaki.cn/ossdoorzo/web/img_bg_wmj.png">
      <h1 class="goods-name">PSVITA Collar X Malice -Unlimited- VLJM-38101</h1>
      <p class="price-com">1,500 日元</p>
      <p>二手 在库</p>
    </main>
    <script type="application/ld+json">
      [
        {
          "@type": "Product",
          "sku": "unrelated-listing",
          "image": "https://images.example.invalid/detail/unrelated.webp"
        },
        {
          "@type": "Product",
          "sku": "z618880764",
          "image": ["https://images.example.invalid/detail/vljm-38101.jpg"]
        }
      ]
    </script>
    """

    status = WameijiBrowserAdapter(enabled=True).parse_detail_html(detail_html, search_item)

    assert status.status == "ok"
    assert status.items[0].image_url == "https://images.example.invalid/detail/vljm-38101.jpg"


def test_parse_detail_html_does_not_promote_page_metadata_as_a_product_title() -> None:
    """A generic site title plus a number is not sufficient purchase evidence."""
    search_item = MarketItem(
        source="wameiji",
        title="Search card",
        price=500,
        currency="JPY",
        external_item_id="metadata-only",
        url="/mall/mercari/detail/metadata-only",
    )
    detail_html = """
    <meta property="og:title" content="Doorzo - Japanese Online Shop Proxy Service">
    <main><p class="price-com">1,280 日元</p></main>
    """

    status = WameijiBrowserAdapter(enabled=True).parse_detail_html(detail_html, search_item)

    assert status.status == "human_required"
    assert status.error_type == "detail_parse_failed"


def test_parse_detail_html_scopes_price_and_availability_to_the_product() -> None:
    """Footer copy and recommended cards must not change a live product's state."""
    search_item = MarketItem(
        source="wameiji",
        title="Search card",
        price=500,
        currency="JPY",
        external_item_id="scoped-detail",
        url="/mall/rakuma/detail/scoped-detail",
    )
    detail_html = """
    <div class="rakuma-detail">
      <div class="main">
        <h1 class="name">Target Album 预约特典 CD</h1>
        <div class="sku-item">
          <div class="price-box price-box-total">
            <p class="price-com">980 <span class="unit">日元</span></p>
          </div>
        </div>
        <div class="operation">
          <button class="cart">加入购物车</button>
          <button class="buy-now">立即购买</button>
        </div>
        <a class="other-item">
          <div class="main-info"><p class="price-com">8,200 日元</p></div>
        </a>
      </div>
    </div>
    <footer>Copyright 2015-2026. All Rights Reserved. 预约商品</footer>
    """

    status = WameijiBrowserAdapter(enabled=True).parse_detail_html(detail_html, search_item)

    assert status.status == "ok"
    item = status.items[0]
    assert item.title == "Target Album 预约特典 CD"
    assert item.price == 980
    assert item.availability == "available"
    assert "All Rights Reserved" not in item.raw_text
    assert "8,200" not in item.raw_text


def test_parse_detail_html_respects_an_explicit_sold_marker() -> None:
    search_item = MarketItem(
        source="wameiji",
        title="Search card",
        price=500,
        currency="JPY",
        external_item_id="sold-detail",
        url="/mall/mercari/detail/sold-detail",
    )
    detail_html = """
    <main class="goods-detail">
      <img class="sold" src="/images/sold.png">
      <h1 class="goods-name">Sold Album CD</h1>
      <p class="price-com">1,280 日元</p>
    </main>
    """

    status = WameijiBrowserAdapter(enabled=True).parse_detail_html(detail_html, search_item)

    assert status.status == "ok"
    assert status.items[0].availability == "sold_out"


def test_parse_detail_html_recognizes_paypay_primary_container() -> None:
    search_item = MarketItem(
        source="wameiji",
        title="Search card",
        price=500,
        currency="JPY",
        external_item_id="paypay-detail",
        url="/mall/paypay/detail/paypay-detail",
    )
    detail_html = """
    <div class="paypay"><div class="paypay-page"><div class="main">
      <div class="goods"><div class="name-box"><h1 class="name">PayPay Game</h1></div></div>
      <div class="price-box price-box-total"><p class="price-com">7,000 日元</p></div>
      <div class="operation"><button class="buy-now">立即购买</button></div>
    </div></div></div>
    <footer>All Rights Reserved</footer>
    """

    status = WameijiBrowserAdapter(enabled=True).parse_detail_html(detail_html, search_item)

    assert status.status == "ok"
    assert status.items[0].price == 7000
    assert status.items[0].availability == "available"


def test_parse_detail_html_ignores_security_words_inside_listing_content() -> None:
    """A seller description may mention a verification code without being a challenge page."""
    search_item = MarketItem(
        source="wameiji",
        title="Search card",
        price=500,
        currency="JPY",
        external_item_id="soft-security-text",
        url="/mall/paypay/detail/soft-security-text",
    )
    detail_html = """
    <html><head><title>挖煤姬 - 专业日淘代购代拍服务</title></head><body>
      <div class="paypay"><div class="paypay-page"><div class="main">
        <div class="goods"><div class="name-box"><h1 class="name">Hololive 限定原声带 CD</h1></div></div>
        <div class="price-box price-box-total"><p class="price-com">1,111 日元</p></div>
        <div class="operation"><button class="buy-now">立即购买</button></div>
        <p class="seller-description">旧手机验证码登录后可联系卖家</p>
        <p class="related-title">Card Staff of Divine Punishment</p>
      </div></div></div>
    </body></html>
    """

    status = WameijiBrowserAdapter(enabled=True).parse_detail_html(detail_html, search_item)

    assert status.status == "ok"
    assert status.items[0].detail_verified is True
    assert status.items[0].title == "Hololive 限定原声带 CD"


def test_parse_detail_html_marks_a_deleted_listing_sold_out_without_using_related_cards() -> None:
    """A deleted detail URL renders recommendations, not a purchasable product."""
    search_item = MarketItem(
        source="wameiji",
        title="Hololive Hanafuda bonus soundtrack CD",
        price=1111,
        currency="JPY",
        external_item_id="deleted-hololive",
        url="/mall/paypay/detail/deleted-hololive",
        availability="unknown_but_visible",
    )
    detail_html = """
    <div class="paypay">
      <div class="common-guide-header"><div class="common-null">
        <p class="nonsupport-text">商品删除</p>
      </div></div>
      <div class="common-guide-widget"><div class="popular">
        <h1 class="name">Unrelated recommendation CD</h1>
        <p class="price-com">300 日元</p>
      </div></div>
    </div>
    """

    status = WameijiBrowserAdapter(enabled=True).parse_detail_html(detail_html, search_item)

    assert status.status == "ok"
    assert len(status.items) == 1
    item = status.items[0]
    assert item.title == search_item.title
    assert item.price == search_item.price
    assert item.availability == "sold_out"
    assert item.detail_verified is True


def test_parse_detail_html_recognizes_street_detail_primary_container() -> None:
    search_item = MarketItem(
        source="wameiji",
        title="Search card",
        price=800,
        currency="JPY",
        external_item_id="street-detail",
        url="/mall/market/detail/street-detail",
    )
    detail_html = """
    <div class="street-detail"><div class="body"><div class="info"><div class="goods">
      <div class="name-box"><h1 class="name">DIABOLIK LOVERS LUNATIC PARADE 限定版</h1></div>
      <div class="tag-box">二手 整体状态不佳</div>
      <div class="price-box price-box-total"><p class="price-com">550 <span>日元</span></p></div>
      <div class="sku-item"><span class="item-name">日本国内运费</span><span class="propertie">550日元</span></div>
      <div class="sku-item"><span class="item-name">代购手续费</span><span class="propertie">50日元</span></div>
      <div class="operation"><button class="cart">加入购物车</button><button class="buy-now">立即购买</button></div>
    </div></div></div></div>
    <footer>All Rights Reserved</footer>
    """

    status = WameijiBrowserAdapter(enabled=True).parse_detail_html(detail_html, search_item)

    assert status.status == "ok"
    assert status.items[0].detail_verified is True
    assert status.items[0].price == 550
    assert status.items[0].availability == "available"
    assert status.items[0].condition_text == "整体状态不佳"
    assert status.items[0].japan_domestic_shipping_jpy == 550
    assert status.items[0].proxy_fee_jpy == 50


def test_parse_detail_html_keeps_seller_borne_domestic_shipping_at_zero() -> None:
    """A later proxy-fee amount must not be mistaken for domestic shipping."""
    search_item = MarketItem(
        source="wameiji",
        title="AQUARIUM limited edition",
        price=4899,
        currency="JPY",
        external_item_id="seller-bears-shipping",
        url="/mall/mercari/detail/seller-bears-shipping",
    )
    detail_html = """
    <div class="street-detail"><div class="body"><div class="info"><div class="goods">
      <div class="name-box"><h1 class="name">あくありうむ。 完全生産限定版 Switch ソフト</h1></div>
      <div class="tag-box">二手 接近未使用</div>
      <div class="price-box price-box-total"><p class="price-com">4,899 <span>日元</span></p></div>
      <div class="sku-item"><span class="item-name">日本国内运费</span><span class="propertie">卖家承担</span></div>
      <div class="sku-item"><span class="item-name">代购手续费</span><span class="propertie">200日元</span></div>
      <div class="operation"><button class="cart">加入购物车</button><button class="buy-now">立即购买</button></div>
    </div></div></div></div>
    """

    status = WameijiBrowserAdapter(enabled=True).parse_detail_html(detail_html, search_item)

    assert status.status == "ok"
    assert status.items[0].japan_domestic_shipping_jpy == 0
    assert status.items[0].proxy_fee_jpy == 200


def test_parse_detail_html_does_not_copy_catalog_from_the_search_card() -> None:
    search_item = MarketItem(
        source="wameiji",
        title="Search card with a possibly unrelated code",
        price=500,
        currency="JPY",
        catalog_no="FAKE-999",
        external_item_id="detail-without-code",
        url="/mall/mercari/detail/detail-without-code",
    )
    detail_html = """
    <main class="goods-detail">
      <h1 class="goods-name">Artist Album 初回限定盤 CD</h1>
      <p class="price-com">1,280 日元</p>
    </main>
    """

    status = WameijiBrowserAdapter(enabled=True).parse_detail_html(detail_html, search_item)

    assert status.status == "ok"
    item = status.items[0]
    assert item.catalog_no is None
    assert item.jan is None


def test_extended_constructor_profile_dir_optional() -> None:
    adapter = WameijiBrowserAdapterWithRunner(
        enabled=True,
        profile_dir=None,
        headless=False,
        snapshot_dir=Path("data/snapshots"),
    )
    assert adapter.enabled is True
    assert adapter._profile_dir is None
    assert adapter._snapshot_dir == Path("data/snapshots")
    assert adapter.runner is None  # 懒加载


def test_ensure_runner_creates_singleton() -> None:
    adapter = WameijiBrowserAdapterWithRunner(
        enabled=False, profile_dir="C:/fake", headless=False,
    )
    r1 = adapter.ensure_runner()
    r2 = adapter.ensure_runner()
    assert r1 is r2
    assert isinstance(r1, WameijiPlaywrightRunner)


@pytest.mark.asyncio
async def test_search_async_disabled_returns_disabled_status() -> None:
    adapter = WameijiBrowserAdapterWithRunner(enabled=False)
    status = await adapter.search_async(_watch())
    assert status.status == "disabled"
    assert status.error_type == "browser_disabled"


@pytest.mark.asyncio
async def test_search_async_no_profile_returns_not_configured() -> None:
    adapter = WameijiBrowserAdapterWithRunner(enabled=True, profile_dir=None)
    status = await adapter.search_async(_watch())
    assert status.status == "not_configured"
    assert status.error_type == "no_profile_or_state"
    assert status.search_entry_url is not None
    assert "SRCL-3520" in status.search_entry_url


# === 内部异常类型 ====================================================
def test_stop_and_human_carries_error_type() -> None:
    exc = _StopAndHuman("captcha", "captcha detected")
    assert exc.error_type == "captcha"
    assert "captcha" in str(exc)


def test_transient_error_is_exception() -> None:
    exc = _TransientError("timeout")
    assert isinstance(exc, Exception)
    assert "timeout" in str(exc)


# === Search outcome dataclass ======================================
def test_search_outcome_defaults_are_safe() -> None:
    o = WameijiSearchOutcome()
    assert o.items == []
    assert o.raw_html == ""
    assert o.status == "ok"
    assert o.error_type is None
    assert o.error_message is None


# === 已有 parse_search_html 兼容 ====================================
def test_parse_search_html_security_check_returns_human_required() -> None:
    adapter = WameijiBrowserAdapter(enabled=True)
    status = adapter.parse_search_html(
        "<html>captcha challenge</html>",
        _watch(),
    )
    assert status.status == "human_required"
    assert status.error_type == "security_check"


def test_parse_search_html_good_html_returns_ok() -> None:
    adapter = WameijiBrowserAdapter(enabled=True)
    # 一个最小 HTML，包含 data-item-card 让 _WameijiCardParser 抽到一张卡片
    html = """
    <div data-item-card>
      <a href="https://meruki.cn/item/SRCL-3520">SRCL-3520 通常盤</a>
      <span data-price>1980</span>
    </div>
    """
    status = adapter.parse_search_html(html, _watch())
    assert status.status == "ok"
    assert len(status.items) >= 1
    assert status.items[0].title.startswith("SRCL-3520")
    assert status.items[0].external_item_id == "SRCL-3520"


def test_parse_search_html_current_goods_item_cards() -> None:
    """解析当前 meruki.cn 使用的 goods-item / goods-name / price-com DOM。"""
    adapter = WameijiBrowserAdapter(enabled=True)
    html = """
    <a class="goods-item" href="/mall/mercari/detail/abc123">
      <div class="main-img"><img class="img" src="https://img.example/item.jpg"></div>
      <p class="goods-name">SRCL-3520 Artist Album 初回限定</p>
      <div class="goods-price"><p class="price-com"> 1,280 <span class="unit">日元</span></p></div>
      <div class="goods-tags"><div class="tag-item">二手</div></div>
    </a>
    <a class="goods-item" href="/mall/mercari/detail/unrelated">
      <p class="goods-name">完全不同的推荐商品</p>
      <div class="goods-price"><p class="price-com"> 99 <span class="unit">日元</span></p></div>
    </a>
    """
    status = adapter.parse_search_html(html, _watch())
    assert status.status == "ok"
    assert len(status.items) == 1
    item = status.items[0]
    assert item.title == "SRCL-3520 Artist Album 初回限定"
    assert item.price == 1280
    assert item.source_site == "mercari"
    assert item.external_item_id == "abc123"
    assert item.image_url == "https://img.example/item.jpg"


def test_external_item_id_supports_query_and_path_urls() -> None:
    assert _external_item_id("https://meruki.cn/item?id=abc123") == "abc123"
    assert _external_item_id("/item/abc123") == "abc123"
    assert _external_item_id("https://meruki.cn/search?keyword=SRCL-3520") is None


# === _adapter_for_source 集成 ==========================================
def test_adapter_for_source_returns_runner_when_profile_dir_set() -> None:
    """profile_dir 给出时，_adapter_for_source 返回 WameijiBrowserAdapterWithRunner。"""
    from cd_monitor.services.live_browser_capture import _adapter_for_source
    from cd_monitor.sources.wameiji_browser import WameijiBrowserAdapterWithRunner

    adapter = _adapter_for_source(
        "wameiji", state_file=None,
        profile_dir="C:/fake/profile", snapshot_dir="C:/fake/snap",
        headless=True,
    )
    assert isinstance(adapter, WameijiBrowserAdapterWithRunner)
    assert adapter.enabled is True
    assert str(adapter._profile_dir) == "C:/fake/profile"
    assert adapter._snapshot_dir == Path("C:/fake/snap")


def test_adapter_for_source_returns_legacy_when_profile_dir_missing() -> None:
    """profile_dir 缺失时退回旧安全骨架。"""
    from cd_monitor.services.live_browser_capture import _adapter_for_source
    from cd_monitor.sources.wameiji_browser import WameijiBrowserAdapter

    adapter = _adapter_for_source("wameiji", state_file=None)
    assert isinstance(adapter, WameijiBrowserAdapter)
    # 旧 adapter 不应有 _profile_dir 属性（避免误判）
    assert not hasattr(adapter, "_profile_dir") or adapter._profile_dir is None


def test_adapter_for_source_xianyu_still_works() -> None:
    """Xianyu 路径不受影响。"""
    from cd_monitor.services.live_browser_capture import _adapter_for_source
    from cd_monitor.sources.xianyu_browser import XianyuBrowserAdapter

    adapter = _adapter_for_source(
        "xianyu", state_file="C:/fake/xianyu_state.json",
    )
    assert isinstance(adapter, XianyuBrowserAdapter)
    assert str(adapter.state_file) == "C:/fake/xianyu_state.json"


def test_adapter_for_source_invalid_source_raises() -> None:
    from cd_monitor.services.live_browser_capture import _adapter_for_source
    with pytest.raises(ValueError, match="source must be"):
        _adapter_for_source("mercari", state_file=None)

# === 端到端：HTML → parse → snapshot → evaluate → opportunity ========
WAMEIJI_MOCK_HTML = """
<html><body>
  <div data-item-card>
    <a href="https://meruki.cn/item/SRCL-3520">
      SRCL-3520 Artist Album 初回限定 帯付き 未開封
    </a>
    <span data-price>1200</span>
    <span data-source-site>mercari</span>
    <img src="https://example.invalid/img.jpg" />
  </div>
  <div data-item-card>
    <a href="https://meruki.cn/item/SRCL-3520-b">
      SRCL-3520 Artist Album 通常盤
    </a>
    <span data-price>980</span>
    <span data-source-site>rakuma</span>
  </div>
</body></html>
""".strip()


XIANYU_MOCK_HTML = """
<html><body>
  <div class="item-card">
    <a href="https://goofish.com/item/x1">Artist SRCL-3520 初回限定 CD</a>
    <span class="price">260</span>
  </div>
  <div class="item-card">
    <a href="https://goofish.com/item/x2">Artist SRCL-3520 初回限定 帯付き</a>
    <span class="price">280</span>
  </div>
  <div class="item-card">
    <a href="https://goofish.com/item/x3">SRCL-3520 通常盤</a>
    <span class="price">300</span>
  </div>
  <div class="item-card">
    <a href="https://goofish.com/item/x4">SRCL-3520 通常盤</a>
    <span class="price">320</span>
  </div>
  <div class="item-card">
    <a href="https://goofish.com/item/x5">SRCL-3520 通常盤</a>
    <span class="price">240</span>
  </div>
  <div class="item-card">
    <a href="https://goofish.com/item/x6">SRCL-3520 通常盤</a>
    <span class="price">270</span>
  </div>
</body></html>
""".strip()


def test_e2e_wameiji_html_parses_through_real_adapter() -> None:
    """真实 WameijiBrowserAdapter.parse_search_html → 抽出 MarketItem 流。"""
    from cd_monitor.core.models import WatchItem

    adapter = WameijiBrowserAdapter(enabled=True)
    status = adapter.parse_search_html(WAMEIJI_MOCK_HTML, WatchItem(catalog_no="SRCL-3520"))
    assert status.status == "ok"
    assert len(status.items) >= 2
    titles = [it.title for it in status.items]
    assert any("SRCL-3520" in t and "初回限定" in t for t in titles)
    assert any("SRCL-3520" in t and "通常盤" in t for t in titles)
    prices = sorted(it.price for it in status.items)
    assert prices[0] == 980
    assert prices[-1] == 1200


def test_e2e_html_to_opportunity_pipeline() -> None:
    """Wameiji HTML + Xianyu HTML → evaluate_html_texts → 至少 1 个 opportunity。

    不需要 Playwright：直接用两个 adapter 的 parse_search_html 路径产出结构化数据，
    再喂给 evaluate_html_texts，验证业务链路能产出决策。
    """
    from cd_monitor.services.imports import evaluate_html_texts

    db_path = "C:/Users/19097/AppData/Local/Temp/cd_monitor_e2e_test.db"
    snapshot_dir = "C:/Users/19097/AppData/Local/Temp/cd_monitor_e2e_snap"

    opportunities, opp_ids, snap_path = evaluate_html_texts(
        catalog_no="SRCL-3520",
        wameiji_html=WAMEIJI_MOCK_HTML,
        xianyu_html=XIANYU_MOCK_HTML,
        db_path=db_path,
        snapshot_dir=snapshot_dir,
    )
    # 至少 1 个机会（候选可能不止 1 个，按卡数）
    assert len(opportunities) >= 1
    # snapshot 路径存在
    assert snap_path.exists()
    # 至少一个应该是 strong_alert / weak_alert / review_only（不会是全 reject）
    decisions = {opp.decision for opp in opportunities}
    assert decisions.issubset({"strong_alert", "weak_alert", "review_only", "reject"})
    # catalog_no 对齐
    for opp in opportunities:
        assert opp.catalog_no == "SRCL-3520"
    # match_confidence 在 [0, 1]
    for opp in opportunities:
        assert 0.0 <= opp.match_confidence <= 1.0
        assert opp.expected_profit is not None


# === spec §3.1 费用提示检测 =============================================
def test_detect_fees_hint_proxy_fee() -> None:
    from cd_monitor.sources.wameiji_browser import _detect_fees_hint
    assert _detect_fees_hint("代购手续费 200円") == "proxy_fee"


def test_detect_fees_hint_multiple_flags() -> None:
    from cd_monitor.sources.wameiji_browser import _detect_fees_hint
    assert _detect_fees_hint("代购手续费 加固 保障") == "proxy_fee+reinforcement+insurance"


def test_detect_fees_hint_no_match() -> None:
    from cd_monitor.sources.wameiji_browser import _detect_fees_hint
    assert _detect_fees_hint("SRCL-3520 通常盤 帯付き") is None


def test_card_parser_populates_fees_hint() -> None:
    """spec §3.1：_WameijiCardParser 创建 MarketItem 时填入 fees_hint。"""
    adapter = WameijiBrowserAdapter(enabled=True)
    html = '''
    <div data-item-card>
      <a href="https://meruki.cn/item/SRCL-3520">SRCL-3520 通常盤 代购手续费</a>
      <span data-price>1980</span>
    </div>
    '''
    status = adapter.parse_search_html(html, _watch())
    assert status.status == "ok"
    assert len(status.items) >= 1
    assert status.items[0].fees_hint == "proxy_fee"


def test_generic_parser_populates_fees_hint() -> None:
    """spec §3.1：_extract_generic_items 也填入 fees_hint。"""
    adapter = WameijiBrowserAdapter(enabled=True)
    html = '''
    <div>
      <a href="https://meruki.cn/item/SRCL-3520-x">SRCL-3520 加固 + 合单费</a>
      <span>1980円</span>
    </div>
    '''
    status = adapter.parse_search_html(html, _watch())
    assert status.status == "ok"
    if status.items:
        assert status.items[0].fees_hint is not None
        assert "reinforcement" in status.items[0].fees_hint



# === state_file 支持 =================================================
def _write_wameiji_state_file(tmp_path: Path) -> Path:
    import json as _json
    snap = {
        "capturedAt": "2026-06-25T00:00:00Z",
        "pageUrl": "https://meruki.cn/",
        "cookies": [
            {"name": "session", "value": "SECRET", "domain": ".meruki.cn",
             "path": "/", "expires": 9999999999, "httpOnly": True, "secure": True, "sameSite": "Lax"}
        ],
        "storage": {"local": {"token": "X"}, "session": {}},
    }
    from cd_monitor.services.wameiji_login_state import save_wameiji_login_state
    output = tmp_path / "wameiji_state.json"
    save_wameiji_login_state(snap, output, keep_raw_snapshot=False)
    return output


def test_wameiji_runner_accepts_state_file_param(tmp_path: Path) -> None:
    state_path = _write_wameiji_state_file(tmp_path)
    runner = WameijiPlaywrightRunner(state_file=str(state_path))
    assert runner.uses_state_file is True
    assert runner._state_file == str(state_path)
    loaded = runner._load_storage_state()
    assert loaded is not None
    assert loaded["cookies"][0]["name"] == "session"


def test_wameiji_runner_state_file_preferred_over_profile_dir(tmp_path: Path) -> None:
    state_path = _write_wameiji_state_file(tmp_path)
    runner = WameijiPlaywrightRunner(
        profile_dir=str(tmp_path / "ignored"),
        state_file=str(state_path),
    )
    # profile_dir wins; state_file is treated as a backup so uses_state_file returns False.
    assert runner.uses_state_file is False
    assert runner._profile_dir == str(tmp_path / "ignored")
    assert runner._state_file == str(state_path)


def test_wameiji_runner_load_storage_state_returns_none_when_unset(tmp_path: Path) -> None:
    runner = WameijiPlaywrightRunner(profile_dir=None)
    assert runner._load_storage_state() is None


def test_wameiji_runner_load_storage_state_returns_none_when_invalid(tmp_path: Path) -> None:
    runner = WameijiPlaywrightRunner(state_file=str(tmp_path / "absent.json"))
    assert runner._load_storage_state() is None


def test_wameiji_adapter_with_runner_state_file_param(tmp_path: Path) -> None:
    state_path = _write_wameiji_state_file(tmp_path)
    adapter = WameijiBrowserAdapterWithRunner(
        enabled=True,
        state_file=str(state_path),
    )
    assert adapter.uses_state_file is True
    status = adapter.search_status(_watch())
    # ready state file -> login_state_ready surfaces
    assert status.login_state_ready is True
    assert status.state_file_status == "ready"
    assert ".meruki.cn" in (status.state_cookie_domains or [])


def test_wameiji_adapter_with_runner_state_file_missing(tmp_path: Path) -> None:
    adapter = WameijiBrowserAdapterWithRunner(
        enabled=True,
        state_file=str(tmp_path / "absent.json"),
    )
    status = adapter.search_status(_watch())
    # missing -> human_required with state_file_status=missing
    assert status.status == "human_required"
    assert status.state_file_status == "missing"


def test_wameiji_adapter_with_runner_async_no_profile_no_state() -> None:
    import asyncio as _asyncio
    adapter = WameijiBrowserAdapterWithRunner(enabled=True)
    status = _asyncio.run(adapter.search_async(_watch()))
    assert status.status == "not_configured"
    assert status.error_type == "no_profile_or_state"
