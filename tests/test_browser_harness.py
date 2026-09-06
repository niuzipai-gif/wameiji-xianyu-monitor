from dataclasses import asdict
import json

from cd_monitor.core.models import WatchItem
from cd_monitor.sources.wameiji_browser import WameijiBrowserAdapter
from cd_monitor.sources.xianyu_browser import XianyuBrowserAdapter


def test_wameiji_html_snapshot_parses_visible_items() -> None:
    html = """
    <div data-item-card>
      <a href="https://example.invalid/item/1">Artist SRCL-3520 初回限定</a>
      <span data-price>¥1,200</span>
      <img src="https://example.invalid/a.jpg" />
      <span data-source-site>mercari</span>
    </div>
    """
    status = WameijiBrowserAdapter(enabled=True).parse_search_html(html, WatchItem("SRCL-3520"))

    assert status.status == "ok"
    assert status.items[0].title == "Artist SRCL-3520 初回限定"
    assert status.items[0].price == 1200
    assert status.items[0].source == "wameiji"


def test_xianyu_html_snapshot_parses_samples() -> None:
    html = """
    <div data-xianyu-card>
      <a href="https://example.invalid/x/1">Artist SRCL-3520 初回限定</a>
      <span data-price>￥260</span>
      <img src="https://example.invalid/x.jpg" />
    </div>
    """
    status = XianyuBrowserAdapter(enabled=True).parse_search_html(html, WatchItem("SRCL-3520"))

    assert status.status == "ok"
    assert status.items[0].title == "Artist SRCL-3520 初回限定"
    assert status.items[0].price_cny == 260


def test_xianyu_current_goofish_feed_cards_parse_title_price_and_image() -> None:
    html = """
    <main class="feeds-list-container--hash">
      <a class="feeds-item-wrap--hash" href="https://www.goofish.com/item?id=1064270240099&categoryId=126862148">
        <div class="feeds-image-container--hash">
          <img class="feeds-image--hash" src="//img.example.invalid/item.webp" />
        </div>
        <div class="feeds-content--hash">
          <div class="row1-wrap-title--hash" title="古内东子 Hourglass SRCL 3520">
            <span class="main-title--hash">古内东子 Hourglass SRCL 3520</span>
          </div>
          <div class="row3-wrap-price--hash">
            <div class="price-wrap--hash"><span class="sign--hash">¥</span><span class="number--hash">27</span><span class="decimal--hash"></span></div>
          </div>
        </div>
      </a>
    </main>
    """

    status = XianyuBrowserAdapter(enabled=True).parse_search_html(html, WatchItem("SRCL-3520"))

    assert status.status == "ok"
    assert len(status.items) == 1
    assert status.items[0].title == "古内东子 Hourglass SRCL 3520"
    assert status.items[0].price_cny == 27
    assert status.items[0].url and "1064270240099" in status.items[0].url
    assert status.items[0].image_url == "//img.example.invalid/item.webp"


def test_browser_harness_parses_nested_visible_cards() -> None:
    wameiji_html = """
    <div data-item-card>
      <div class="media"><img src="https://example.invalid/a.jpg" /></div>
      <div class="body">
        <a href="https://example.invalid/item/1"><span>Artist SRCL-3520 初回限定</span></a>
        <div class="meta"><span data-price>¥1,200</span></div>
        <span data-source-site>mercari</span>
      </div>
    </div>
    """
    xianyu_html = """
    <div data-xianyu-card>
      <div class="cover"><img src="https://example.invalid/x.jpg" /></div>
      <div class="body">
        <a href="https://example.invalid/x/1"><span>Artist SRCL-3520 初回限定</span></a>
        <div class="price"><span data-price>￥260</span></div>
      </div>
    </div>
    """

    wameiji = WameijiBrowserAdapter(enabled=True).parse_search_html(
        wameiji_html,
        WatchItem("SRCL-3520"),
    )
    xianyu = XianyuBrowserAdapter(enabled=True).parse_search_html(
        xianyu_html,
        WatchItem("SRCL-3520"),
    )

    assert wameiji.status == "ok"
    assert wameiji.items[0].title == "Artist SRCL-3520 初回限定"
    assert wameiji.items[0].price == 1200
    assert wameiji.items[0].source_site == "mercari"
    assert xianyu.status == "ok"
    assert xianyu.items[0].title == "Artist SRCL-3520 初回限定"
    assert xianyu.items[0].price_cny == 260


def test_browser_harness_parses_generic_saved_search_html() -> None:
    wameiji_html = """
    <section class="result">
      <div class="item">
        <a href="https://example.invalid/item/1">Artist SRCL-3520 初回限定 帯付き</a>
        <p>Mercari visible listing</p>
        <strong>¥1,200</strong>
      </div>
    </section>
    """
    xianyu_html = """
    <main>
      <article>
        <a href="https://example.invalid/x/1">Artist SRCL-3520 初回限定</a>
        <span>￥260</span>
      </article>
      <article>
        <a href="https://example.invalid/x/2">Artist SRCL-3520 通常盤</a>
        <span>￥280</span>
      </article>
    </main>
    """

    wameiji = WameijiBrowserAdapter(enabled=True).parse_search_html(
        wameiji_html,
        WatchItem("SRCL-3520"),
    )
    xianyu = XianyuBrowserAdapter(enabled=True).parse_search_html(
        xianyu_html,
        WatchItem("SRCL-3520"),
    )

    assert wameiji.status == "ok"
    assert wameiji.items[0].title == "Artist SRCL-3520 初回限定 帯付き"
    assert wameiji.items[0].price == 1200
    assert xianyu.status == "ok"
    assert [sample.price_cny for sample in xianyu.items] == [260, 280]


def test_browser_harness_parses_common_real_world_price_text() -> None:
    wameiji_html = """
    <section class="search-results">
      <article>
        <a href="https://example.invalid/item/real-1">Artist SRCL-3520 初回限定 帯付き</a>
        <span>価格 1,200円 税込</span>
      </article>
      <article>
        <a href="https://example.invalid/item/real-2">Artist SRCL-3520 通常盤</a>
        <span>JPY 980</span>
      </article>
    </section>
    """
    xianyu_html = """
    <main>
      <article>
        <a href="https://example.invalid/x/real-1">Artist SRCL-3520 初回限定</a>
        <span>到手价 260元</span>
      </article>
      <article>
        <a href="https://example.invalid/x/real-2">Artist SRCL-3520 通常盘</a>
        <span>CNY 280</span>
      </article>
      <article>
        <a href="https://example.invalid/x/real-3">Artist SRCL-3520 未拆</a>
        <span>RMB 300</span>
      </article>
    </main>
    """

    wameiji = WameijiBrowserAdapter(enabled=True).parse_search_html(
        wameiji_html,
        WatchItem("SRCL-3520"),
    )
    xianyu = XianyuBrowserAdapter(enabled=True).parse_search_html(
        xianyu_html,
        WatchItem("SRCL-3520"),
    )

    assert wameiji.status == "ok"
    assert [item.price for item in wameiji.items] == [1200, 980]
    assert xianyu.status == "ok"
    assert [sample.price_cny for sample in xianyu.items] == [260, 280, 300]


def test_browser_harness_parses_wameiji_riyuan_price_text() -> None:
    html = """
    <section>
      <article>
        <a href="https://example.invalid/item/riyuan-1">Artist SRCL-3520 初回限定 帯付き</a>
        <span>2,000 日元</span>
      </article>
    </section>
    """

    status = WameijiBrowserAdapter(enabled=True).parse_search_html(html, WatchItem("SRCL-3520"))

    assert status.status == "ok"
    assert status.items[0].price == 2000


def test_browser_harness_parses_attribute_text_and_lazy_images() -> None:
    wameiji_html = """
    <section>
      <article>
        <a href="https://example.invalid/item/attr-1" title="Artist SRCL-3520 初回限定 帯付き"></a>
        <img data-src="https://example.invalid/lazy-wameiji.jpg" alt="Artist SRCL-3520 cover" />
        <span aria-label="価格 1,200円"></span>
      </article>
    </section>
    """
    xianyu_html = """
    <main>
      <article>
        <a href="https://example.invalid/x/attr-1" aria-label="Artist SRCL-3520 初回限定"></a>
        <img data-original="https://example.invalid/lazy-xianyu.jpg" alt="Artist SRCL-3520 cover" />
        <span title="到手价 260元"></span>
      </article>
    </main>
    """

    wameiji = WameijiBrowserAdapter(enabled=True).parse_search_html(
        wameiji_html,
        WatchItem("SRCL-3520"),
    )
    xianyu = XianyuBrowserAdapter(enabled=True).parse_search_html(
        xianyu_html,
        WatchItem("SRCL-3520"),
    )

    assert wameiji.status == "ok"
    assert wameiji.items[0].title == "Artist SRCL-3520 初回限定 帯付き"
    assert wameiji.items[0].price == 1200
    assert wameiji.items[0].image_url == "https://example.invalid/lazy-wameiji.jpg"
    assert xianyu.status == "ok"
    assert xianyu.items[0].title == "Artist SRCL-3520 初回限定"
    assert xianyu.items[0].price_cny == 260
    assert xianyu.items[0].image_url == "https://example.invalid/lazy-xianyu.jpg"


def test_browser_harness_marks_sold_out_generic_wameiji_items() -> None:
    html = """
    <section>
      <article>
        <a href="https://example.invalid/item/sold-1">Artist SRCL-3520 初回限定 帯付き</a>
        <span>価格 1,200円</span>
        <span>売り切れ</span>
      </article>
    </section>
    """

    status = WameijiBrowserAdapter(enabled=True).parse_search_html(html, WatchItem("SRCL-3520"))

    assert status.status == "ok"
    assert status.items[0].availability == "sold_out"


def test_browser_harness_extracts_generic_wameiji_source_and_condition() -> None:
    html = """
    <section>
      <article>
        <a href="https://example.invalid/item/source-1">Artist SRCL-3520 初回限定 帯付き</a>
        <span>Mercari visible listing</span>
        <span>価格 1,200円</span>
        <span>盤傷 ケース割れあり</span>
      </article>
    </section>
    """

    status = WameijiBrowserAdapter(enabled=True).parse_search_html(html, WatchItem("SRCL-3520"))

    assert status.status == "ok"
    assert status.items[0].source_site == "mercari"
    assert status.items[0].condition_text == "盤傷 ケース割れあり"


def test_browser_harness_returns_human_required_on_security_text() -> None:
    html = "<html>请完成 CAPTCHA 安全验证后继续</html>"
    status = WameijiBrowserAdapter(enabled=True).parse_search_html(html, WatchItem("SRCL-3520"))

    assert status.error_type == "security_check"


def test_browser_harness_returns_human_required_on_community_risk_tokens() -> None:
    html = """
    <html>
      <body>
        {"ret":["FAIL_SYS_USER_VALIDATE","RGV587_ERROR::SM::哎哟喂,被挤爆啦,请稍后重试"],
         "data":{"url":"https://acs.example.invalid/_____tmd_____/punish?x5step=2"}}
      </body>
    </html>
    """

    wameiji = WameijiBrowserAdapter(enabled=True).parse_search_html(html, WatchItem("SRCL-3520"))
    xianyu = XianyuBrowserAdapter(enabled=True).parse_search_html(html, WatchItem("SRCL-3520"))

    assert wameiji.error_type == "security_check"
    assert xianyu.status == "human_required"
    assert xianyu.error_type == "security_check"


def test_browser_harness_enabled_status_exposes_safe_manual_entry_points() -> None:
    watch = WatchItem("SRCL-3520")

    wameiji = WameijiBrowserAdapter(enabled=True).search_status(watch)
    xianyu = XianyuBrowserAdapter(enabled=True).search_status(watch)

    assert "meruki.cn" in (wameiji.search_entry_url or "")
    assert "visible search result HTML" in (wameiji.capture_instruction or "")
    assert xianyu.status == "human_required"
    assert "goofish.com/search" in (xianyu.search_entry_url or "")
    assert "visible search result HTML" in (xianyu.capture_instruction or "")


def test_xianyu_status_reports_ready_playwright_storage_state_without_cookie_values(tmp_path) -> None:
    state_path = tmp_path / "xianyu_state.json"
    state_path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "cookie2",
                        "value": "SECRET-COOKIE-VALUE",
                        "domain": ".goofish.com",
                        "path": "/",
                    },
                    {
                        "name": "_tb_token_",
                        "value": "SECRET-TB-VALUE",
                        "domain": ".taobao.com",
                        "path": "/",
                    },
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )

    status = XianyuBrowserAdapter(enabled=True, state_file=state_path).search_status(
        WatchItem("SRCL-3520"),
    )

    assert status.error_type == "executor_missing"
    assert status.login_state_ready is True
    assert status.state_file_status == "ready"
    assert status.state_file_path == str(state_path)
    assert status.state_cookie_domains == [".goofish.com", ".taobao.com"]
    assert "storage_state ready" in (status.error_message or "")
    assert "SECRET-COOKIE-VALUE" not in json.dumps(asdict(status))
    assert "SECRET-TB-VALUE" not in json.dumps(asdict(status))


def test_xianyu_status_rejects_invalid_storage_state_file(tmp_path) -> None:
    state_path = tmp_path / "xianyu_state.json"
    state_path.write_text('{"cookies": [{"name": "cookie2", "value": "SECRET"}]}', encoding="utf-8")

    status = XianyuBrowserAdapter(enabled=True, state_file=state_path).search_status(
        WatchItem("SRCL-3520"),
    )

    assert status.error_type == "invalid_state_file"
    assert status.login_state_ready is False
    assert status.state_file_status == "invalid"
    assert "SECRET" not in json.dumps(asdict(status))
