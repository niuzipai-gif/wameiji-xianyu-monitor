"""真实场景的 meruki.cn HTML 夹具测试（spec §3.1）。

覆盖：
1. sold-out 检测
2. 多卡片页（含预售、已售、可购买混合）
3. 日文-only 文本（中文用户用翻译工具看，但 meruki 是中文站，部分卖家用纯日文）
4. fees_hint 在真实 HTML 中被正确抽到
5. condition_text 抽到盘伤
"""
from cd_monitor.core.models import WatchItem
from cd_monitor.sources.wameiji_browser import WameijiBrowserAdapter


def _adapter():
    return WameijiBrowserAdapter(enabled=True)


def test_sold_out_card_marked_as_sold_out() -> None:
    """卖完的卡片 availability 应该是 sold_out。"""
    html = '''
    <div data-item-card>
      <a href="https://meruki.cn/item/SRCL-3520-a">SRCL-3520 初回限定 帯付き</a>
      <span data-price>1500</span>
      <span data-source-site>mercari</span>
      <span class="badge">売約済</span>
    </div>
    '''
    status = _adapter().parse_search_html(html, WatchItem(catalog_no="SRCL-3520"))
    assert status.status == "ok"
    assert len(status.items) == 1
    assert status.items[0].availability == "sold_out"


def test_mixed_availability_cards() -> None:
    """一搜结果页里同时有 sold_out / reserved / available / unknown。"""
    html = '''
    <div data-item-card>
      <a href="https://meruki.cn/item/SRCL-3520-1">SRCL-3520 初回限定</a>
      <span data-price>1000</span>
      <span class="status">販売中</span>
    </div>
    <div data-item-card>
      <a href="https://meruki.cn/item/SRCL-3520-2">SRCL-3520 通常盤</a>
      <span data-price>800</span>
      <span class="status">予約</span>
    </div>
    <div data-item-card>
      <a href="https://meruki.cn/item/SRCL-3520-3">SRCL-3520 特典付き</a>
      <span data-price>1200</span>
      <span class="status">売り切れ</span>
    </div>
    <div data-item-card>
      <a href="https://meruki.cn/item/SRCL-3520-4">SRCL-3520 通常盤</a>
      <span data-price>950</span>
    </div>
    '''
    status = _adapter().parse_search_html(html, WatchItem(catalog_no="SRCL-3520"))
    assert status.status == "ok"
    assert len(status.items) == 4
    avail = {it.availability for it in status.items}
    assert "available" in avail
    assert "reserved" in avail
    assert "sold_out" in avail
    assert "unknown_but_visible" in avail


def test_japanese_only_card_via_generic_parser() -> None:
    """纯日文标题（无中文翻译）走 _extract_generic_items 路径。"""
    html = '''
    <div class="product-card">
      <a href="https://meruki.cn/item/SRCL-3520-jp">【新品】SRCL-3520 初回限定盤 帯付き 未開封</a>
      <span>1,200円</span>
    </div>
    <div class="product-card">
      <a href="https://meruki.cn/item/SRCL-3520-jp2">SRCL-3520 通常盤 帯付き</a>
      <span>980円</span>
    </div>
    '''
    status = _adapter().parse_search_html(html, WatchItem(catalog_no="SRCL-3520"))
    assert status.status == "ok"
    assert len(status.items) >= 1
    titles = [it.title for it in status.items]
    assert any("初回限定" in t for t in titles)


def test_fees_hint_extracted_from_real_card() -> None:
    """真实卡片中提到代购手续费时 fees_hint 应被识别。"""
    html = '''
    <div data-item-card>
      <a href="https://meruki.cn/item/SRCL-3520-fee">SRCL-3520 初回限定 帯付き</a>
      <span data-price>1000</span>
      <span data-source-site>mercari</span>
      <p class="note">代购手续费 200円 / 加固 100円 / 保障 50円</p>
    </div>
    '''
    status = _adapter().parse_search_html(html, WatchItem(catalog_no="SRCL-3520"))
    assert status.status == "ok"
    assert len(status.items) == 1
    item = status.items[0]
    assert item.fees_hint is not None
    assert "proxy_fee" in item.fees_hint
    assert "reinforcement" in item.fees_hint
    assert "insurance" in item.fees_hint


def test_condition_text_extracted_when_disc_scratch() -> None:
    """盘伤/ケース割れ等 condition 文本被抽到 condition_text 字段。"""
    html = '''
    <div data-item-card>
      <a href="https://meruki.cn/item/SRCL-3520-cond">SRCL-3520 通常盤</a>
      <span data-price>600</span>
      <span class="condition">盤傷あり ケースに軽微な傷</span>
    </div>
    '''
    status = _adapter().parse_search_html(html, WatchItem(catalog_no="SRCL-3520"))
    assert status.status == "ok"
    assert len(status.items) == 1
    item = status.items[0]
    # condition_text 字段会被填充（盘伤/傷 触发）
    assert item.condition_text is not None
    assert "盤傷" in item.condition_text or "傷" in item.condition_text


def test_source_site_mercari_vs_rakuma() -> None:
    """不同 source site 在同一页出现时都能被识别。"""
    html = '''
    <div data-item-card>
      <a href="https://meruki.cn/item/SRCL-3520-m">SRCL-3520 初回限定</a>
      <span data-price>1100</span>
      <span data-source-site>mercari</span>
    </div>
    <div data-item-card>
      <a href="https://meruki.cn/item/SRCL-3520-r">SRCL-3520 通常盤</a>
      <span data-price>900</span>
      <span data-source-site>rakuma</span>
    </div>
    <div data-item-card>
      <a href="https://meruki.cn/item/SRCL-3520-b">SRCL-3520 帯付き</a>
      <span data-price>1050</span>
      <span data-source-site>bookoff</span>
    </div>
    '''
    status = _adapter().parse_search_html(html, WatchItem(catalog_no="SRCL-3520"))
    sites = {it.source_site for it in status.items}
    assert {"mercari", "rakuma", "bookoff"}.issubset(sites)


def test_empty_result_page_returns_ok_with_no_items() -> None:
    """空结果页（没有匹配卡片）返回 ok 但 items 为空。"""
    html = '''
    <html><body><div class="empty">没有找到匹配的商品</div></body></html>
    '''
    status = _adapter().parse_search_html(html, WatchItem(catalog_no="SRCL-3520"))
    assert status.status == "ok"
    assert status.items == []