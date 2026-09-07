from __future__ import annotations

from cd_monitor.core.models import WatchItem
from cd_monitor.sources.wameiji_browser import WameijiBrowserAdapter
from cd_monitor.sources.xianyu_browser import XianyuBrowserAdapter


def test_wameiji_keyword_result_cards_are_kept_without_catalog_number() -> None:
    html = """
    <div data-item-card><a href="https://meruki.cn/item/1">Artist Album 初回限定盤</a><span data-price>1200</span></div>
    <div data-item-card><a href="https://meruki.cn/item/2">Another Artist 初回限定盤</a><span data-price>900</span></div>
    """

    result = WameijiBrowserAdapter(enabled=True).parse_search_html(
        html, WatchItem(catalog_no="初回限定盤")
    )

    assert result.status == "ok"
    assert [item.title for item in result.items] == [
        "Artist Album 初回限定盤",
        "Another Artist 初回限定盤",
    ]


def test_wameiji_generic_discovery_keeps_cards_that_do_not_repeat_every_search_term() -> None:
    html = """
    <div data-item-card><a href="https://meruki.cn/item/1">Nintendo Switch 特典付きゲーム</a><span data-price>1200</span></div>
    <div data-item-card><a href="https://meruki.cn/item/2">Switch ソフト 限定BOX</a><span data-price>900</span></div>
    """

    result = WameijiBrowserAdapter(enabled=True).parse_search_html(
        html, WatchItem(catalog_no="Switch 限定版")
    )

    assert [item.title for item in result.items] == [
        "Nintendo Switch 特典付きゲーム",
        "Switch ソフト 限定BOX",
    ]


def test_xianyu_title_query_keeps_matching_cards_but_rejects_unrelated_card() -> None:
    html = """
    <a data-xianyu-card href="https://goofish.com/item/1"><span class="row1-wrap-title-x">Artist Album 初回限定盤</span><span data-price="300"></span></a>
    <a data-xianyu-card href="https://goofish.com/item/2"><span class="row1-wrap-title-x">Other Album 通常盤</span><span data-price="320"></span></a>
    """

    result = XianyuBrowserAdapter(enabled=True).parse_search_html(
        html, WatchItem(catalog_no="Artist Album")
    )

    assert result.status == "ok"
    assert [(sample.title, sample.price_cny) for sample in result.items] == [
        ("Artist Album 初回限定盤", 300.0)
    ]


def test_xianyu_title_query_keeps_same_product_when_condition_text_differs() -> None:
    html = """
    <a data-xianyu-card href="https://goofish.com/item/1"><span class="row1-wrap-title-x">King Prince So Honey EP 通常盤 CD</span><span data-price="300"></span></a>
    <a data-xianyu-card href="https://goofish.com/item/2"><span class="row1-wrap-title-x">Snow Man 初回限定盤 CD Blu-ray</span><span data-price="320"></span></a>
    """

    result = XianyuBrowserAdapter(enabled=True).parse_search_html(
        html,
        WatchItem(
            catalog_no="初回限定盤A Blu-ray付 トレカAタイプ1種 King&Prince CD+Blu-ray/So Honey EP"
        ),
    )

    assert [(sample.title, sample.price_cny) for sample in result.items] == [
        ("King Prince So Honey EP 通常盤 CD", 300.0)
    ]


def test_xianyu_title_query_rejects_cards_when_only_generic_media_terms_overlap() -> None:
    html = """
    <a data-xianyu-card href="https://goofish.com/item/1"><span class="row1-wrap-title-x">Other Artist 初回限定盤 CD Blu-ray</span><span data-price="300"></span></a>
    """

    result = XianyuBrowserAdapter(enabled=True).parse_search_html(
        html, WatchItem(catalog_no="初回限定盤 CD Blu-ray")
    )

    assert result.items == []


def test_catalog_query_remains_strict_for_wameiji_cards() -> None:
    html = """
    <div data-item-card><a href="https://meruki.cn/item/1">Artist SRCL-3520 初回限定盤</a><span data-price>1200</span></div>
    <div data-item-card><a href="https://meruki.cn/item/2">Artist SRCL-9999 初回限定盤</a><span data-price>900</span></div>
    """

    result = WameijiBrowserAdapter(enabled=True).parse_search_html(
        html, WatchItem(catalog_no="SRCL-3520")
    )

    assert [item.title for item in result.items] == ["Artist SRCL-3520 初回限定盤"]
