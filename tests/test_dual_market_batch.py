from __future__ import annotations

from dataclasses import replace

from cd_monitor.core.dual_market import ListingObservation
from cd_monitor.core.models import MarketItem, XianyuPriceSample
from cd_monitor.services.dual_market_batch import (
    build_reverse_discovery_candidate,
    choose_xianyu_pair,
    title_query_without_identifiers,
    wameiji_item_matches_seed,
)


def wameiji_observation(**changes: object) -> ListingObservation:
    base = ListingObservation(
        source="wameiji",
        source_listing_id="w-1",
        canonical_product_key="catalog:nzs954|edition:initial_limited|condition:sealed",
        title="M!LK Kiss Plan 初回限定盤A CD+Blu-ray NZS-954",
        price=2200,
        currency="JPY",
        url="https://meruki.cn/mall/paypay/detail/w-1",
        image_url="https://auctions.c.yimg.jp/w-1.jpg",
        availability="available",
        condition_group="sealed",
        completeness="complete",
        evidence_level="detail_verified",
        captured_at="2026-09-09T01:00:00+08:00",
    )
    return replace(base, **changes)


def xianyu_sample(
    listing_id: str,
    *,
    title: str = "M!LK Kiss Plan 初回限定盘A NZS-954 全新未拆封",
    price: float = 129,
    raw_text: str = "全新未拆封 NZS-954 初回限定盘A",
) -> XianyuPriceSample:
    return XianyuPriceSample(
        catalog_no="NZS-954",
        title=title,
        price_cny=price,
        url=f"https://www.goofish.com/item?id={listing_id}",
        image_url=f"https://img.alicdn.com/{listing_id}.jpg",
        raw_text=raw_text,
    )


def test_structured_pair_chooses_lowest_same_condition_listing() -> None:
    selection = choose_xianyu_pair(
        wameiji_observation(),
        [
            xianyu_sample("higher", price=168),
            xianyu_sample("lowest", price=129),
            xianyu_sample("used", price=88, raw_text="二手有划痕 NZS-954"),
        ],
        captured_at="2026-09-09T01:01:00+08:00",
        snapshot_path="runs/xianyu-search.html",
        screenshot_path="runs/xianyu-search.png",
    )

    assert selection is not None
    assert selection.match_kind == "structured_identifier"
    assert selection.matched_listing_count == 2
    assert selection.xianyu.source_listing_id == "lowest"
    assert selection.xianyu.price == 129
    assert selection.xianyu.canonical_product_key == selection.wameiji.canonical_product_key


def test_structured_pair_ignores_cross_border_proxy_quote_with_shipping_due_later() -> None:
    proxy = xianyu_sample(
        "proxy",
        price=39,
        title="M!LK Kiss Plan 初回限定盘A NZS-954 日本代购",
        raw_text=(
            "全新未拆封，日本直邮，后续需要您单独支付国际运费和税费，"
            "到手价预估详询客服 NZS-954 初回限定盘A"
        ),
    )
    local_resale = xianyu_sample("local", price=129)

    selection = choose_xianyu_pair(
        wameiji_observation(),
        [proxy, local_resale],
        captured_at="2026-09-09T01:01:00+08:00",
    )

    assert selection is not None
    assert selection.xianyu.source_listing_id == "local"
    assert selection.matched_listing_count == 1


def test_structured_pair_ignores_multi_item_listing_whose_card_price_is_ambiguous() -> None:
    multi_item = xianyu_sample(
        "multi",
        price=20,
        title=(
            "ARASHI 岚 演唱会DVD CD Japonism 初回限定盘 50 "
            "I seek Daylight 通常盘 20 Are You Happy DVD 50"
        ),
        raw_text="全新未拆封 JACA-5480 初回限定盘",
    )
    direct_listing = xianyu_sample("direct", price=129)

    selection = choose_xianyu_pair(
        wameiji_observation(),
        [multi_item, direct_listing],
        captured_at="2026-09-09T01:01:00+08:00",
    )

    assert selection is not None
    assert selection.xianyu.source_listing_id == "direct"
    assert selection.matched_listing_count == 1


def test_pair_rejects_a_multi_disc_resale_bundle_as_a_single_album_price() -> None:
    source = wameiji_observation(
        canonical_product_key="jan:4988003217372",
        title="國府田マリ子 / だいすきなうた [CD]",
        condition_group="complete_used",
    )
    bundle = XianyuPriceSample(
        catalog_no=None,
        title=(
            "日版CD《うた☆だいすき！》《ないないばあっ！》两碟合售，"
            "NHK儿童节目原声，带侧标，播放正常"
        ),
        price_cny=15,
        url="https://www.goofish.com/item?id=two-disc-bundle",
        image_url="https://img.alicdn.com/two-disc-bundle.jpg",
        raw_text="两张不同CD一起打包出售",
    )

    assert (
        choose_xianyu_pair(
            source,
            [bundle],
            captured_at="2026-09-09T01:01:00+08:00",
            minimum_title_samples=1,
            required_variant_title="うた だいすき",
            required_media_type="cd",
        )
        is None
    )


def test_pair_ignores_cheaper_digital_download_listing() -> None:
    source = wameiji_observation(
        canonical_product_key="jan:4988615040443|platform:psvita",
        title="PSVita／真・三國無双 NEXT",
        condition_group="complete_used",
    )
    digital = XianyuPriceSample(
        catalog_no="PS Vita 真三国无双 NEXT",
        title="[自动发货] 热血三国志 Three Kingdoms Next 中文全DLC",
        price_cny=1,
        url="https://www.goofish.com/item?id=digital",
        image_url="https://img.alicdn.com/digital.jpg",
        raw_text="网盘下载 免安装 解压即玩 免Steam",
    )
    physical = XianyuPriceSample(
        catalog_no="PS Vita 真三国无双 NEXT",
        title="PSVITA 真三国无双 NEXT 日版游戏卡带",
        price_cny=40,
        url="https://www.goofish.com/item?id=physical",
        image_url="https://img.alicdn.com/physical.jpg",
        raw_text="裸卡无盒，卡带正常读取，二手现货",
    )

    selection = choose_xianyu_pair(
        source,
        [digital, physical],
        captured_at="2026-09-09T01:01:00+08:00",
        minimum_title_samples=1,
        required_variant_title="PSVITA 真三国无双NEXT",
    )

    assert selection is not None
    assert selection.xianyu.source_listing_id == "physical"
    assert selection.xianyu.price == 40


def test_title_pair_rejects_numbered_multi_product_menu() -> None:
    source = wameiji_observation(
        canonical_product_key="catalog:jaca5991",
        title="1st Love / なにわ男子",
        condition_group="complete_used",
    )
    menu = XianyuPriceSample(
        catalog_no="なにわ男子 1st Love",
        title=(
            "なにわ男子 专辑 从左至右 从上至下 "
            "1.初心LOVE罗森限定盘 2.初心LOVE初回限定盘2 "
            "3.1st LOVE通常盘 4.The Answer初回限定盘1"
        ),
        price_cny=20,
        url="https://www.goofish.com/item?id=menu",
        image_url="https://img.alicdn.com/menu.jpg",
        raw_text="多张专辑，私聊选编号",
    )

    assert (
        choose_xianyu_pair(
            source,
            [menu],
            captured_at="2026-09-09T01:01:00+08:00",
            minimum_title_samples=1,
        )
        is None
    )


def test_title_pair_rejects_different_initial_limited_number() -> None:
    source = wameiji_observation(
        canonical_product_key="catalog:jaca5212|edition:initial_limited",
        title="KAT-TUN Going! 初回限定盤1",
        condition_group="complete_used",
    )
    other_variant = XianyuPriceSample(
        catalog_no="KAT-TUN Going",
        title="KAT-TUN Going! 初回限定盘2 日版CD",
        price_cny=77,
        url="https://www.goofish.com/item?id=initial-2",
        image_url="https://img.alicdn.com/initial-2.jpg",
        raw_text="初回限定盘2，带原包装",
    )

    assert (
        choose_xianyu_pair(
            source,
            [other_variant],
            captured_at="2026-09-09T01:01:00+08:00",
            minimum_title_samples=1,
        )
        is None
    )


def test_title_pair_rejects_initial_limited_against_anime_limited() -> None:
    source = wameiji_observation(
        canonical_product_key="jan:4547557011791|edition:initial_limited",
        title="Overfly 初回生産限定盤 Blu-ray付 / 春奈るな",
        condition_group="complete_used",
    )


def test_structured_source_rejects_unrelated_vita_title_only_sample() -> None:
    source = wameiji_observation(
        canonical_product_key="catalog:vljm30076|platform:psvita",
        title="【PS Vita】剣の街の異邦人 ～黒の宮殿～",
        condition_group="complete_used",
    )
    unrelated = XianyuPriceSample(
        catalog_no="PS Vita 刀剑",
        title="刀剑神域 失落之歌 日文 单卡 PS Vita 日版游戏",
        price_cny=20,
        url="https://www.goofish.com/item?id=wrong-vita-game",
        image_url="https://img.alicdn.com/wrong-vita-game.jpg",
        raw_text="中古游戏盘",
    )

    assert (
        choose_xianyu_pair(
            source,
            [unrelated],
            captured_at="2026-09-09T01:01:00+08:00",
            minimum_title_samples=1,
            required_variant_title="PS Vita 刀剑神域 虚空断章",
        )
        is None
    )
    anime_variant = XianyuPriceSample(
        catalog_no="春奈露娜 Overfly",
        title="春奈露娜 Overfly 动画限定盘 CD+DVD",
        price_cny=40,
        url="https://www.goofish.com/item?id=anime-limited",
        image_url="https://img.alicdn.com/anime-limited.jpg",
        raw_text="刀剑神域动画限定盘，正版日版CD",
    )

    assert (
        choose_xianyu_pair(
            source,
            [anime_variant],
            captured_at="2026-09-09T01:01:00+08:00",
            minimum_title_samples=1,
        )
        is None
    )


def test_wameiji_seed_rejects_different_limited_edition_family() -> None:
    item = MarketItem(
        source="wameiji",
        title="Overfly 初回生産限定盤 Blu-ray付 / 春奈るな",
        price=605,
        currency="JPY",
        url="https://meruki.cn/mall/rakuten/detail/overfly",
        raw_text="初回生産限定盤 Blu-ray Disc付",
    )

    assert not wameiji_item_matches_seed(
        item,
        seed_title="春奈露娜 Overfly 动画限定盘 CD+DVD",
        query="春奈露娜 Overfly",
        query_kind="strict_title",
    )


def test_wameiji_seed_rejects_same_artist_with_a_different_japanese_title() -> None:
    item = MarketItem(
        source="wameiji",
        title="【中古】願い / RYTHEM [CD]",
        price=295,
        currency="JPY",
        url="https://meruki.cn/mall/rakuten/detail/rythem-negai",
        raw_text="RYTHEM 願い CD",
    )

    assert not wameiji_item_matches_seed(
        item,
        seed_title="日版CD专辑 RYTHEM ホウキ雲",
        query="RYTHEM ホウキ雲",
        query_kind="strict_title",
        seed_media_type="cd",
    )


def test_wameiji_seed_rejects_a_different_numbered_release_in_the_same_series() -> None:
    item = MarketItem(
        source="wameiji",
        title="ホームクラシック名曲集 Concert 4 新世界より 交響曲集 CD",
        price=500,
        currency="JPY",
        url="https://meruki.cn/mall/rakuma/detail/concert-4",
        raw_text="中古CD 品番 OCD-12004",
    )

    assert not wameiji_item_matches_seed(
        item,
        seed_title="Concert名曲集 1992年 服部克久监制 1-3",
        query="Concert名曲集 服部克久",
        query_kind="strict_title",
        seed_media_type="cd",
    )


def test_numbered_release_accepts_equivalent_arabic_and_roman_numerals() -> None:
    item = MarketItem(
        source="wameiji",
        title="Fab Gear 2 [CD]",
        price=220,
        currency="JPY",
        url="https://meruki.cn/mall/rakuten/detail/fab-gear-2",
        raw_text="中古CD おすすめ商品 Fab Gear 3 も販売中",
    )

    assert wameiji_item_matches_seed(
        item,
        seed_title="Bend it Fab Gear II 日版CD",
        query="Fab Gear II",
        query_kind="strict_title",
        seed_media_type="cd",
    )


def test_reverse_seed_preserves_initial_limited_number() -> None:
    sample = xianyu_sample(
        "kat-tun-going-2",
        title="KAT-TUN Going! 初回限定盘2 日版CD",
        price=77,
        raw_text="初回限定盘2，带原包装",
    )

    screened = build_reverse_discovery_candidate(sample)

    assert screened.candidate is not None
    assert screened.candidate.seed_title.endswith("初回限定版2")


def test_structured_source_accepts_two_corroborating_title_only_resale_cards() -> None:
    source = wameiji_observation(
        canonical_product_key="catalog:jaca5480|edition:initial_limited",
        title="嵐 Japonism DVD付初回限定盤 JACA-5480",
        condition_group="complete_used",
    )
    samples = [
        XianyuPriceSample(
            catalog_no="嵐 Japonism",
            title="嵐 ARASHI Japonism 初回限定盘 CD+DVD",
            price_cny=88,
            url="https://www.goofish.com/item?id=domestic-1",
            image_url="https://img.alicdn.com/domestic-1.jpg",
            raw_text="二手现货 初回限定盘",
        ),
        XianyuPriceSample(
            catalog_no="嵐 Japonism",
            title="ARASHI Japonism CD DVD 初回限定盘",
            price_cny=79,
            url="https://www.goofish.com/item?id=domestic-2",
            image_url="https://img.alicdn.com/domestic-2.jpg",
            raw_text="国内现货 初回限定盘",
        ),
    ]

    selection = choose_xianyu_pair(
        source,
        samples,
        captured_at="2026-09-09T01:01:00+08:00",
        minimum_title_samples=2,
    )

    assert selection is not None
    assert selection.match_kind == "strict_title_corroborated"
    assert selection.matched_listing_count == 2
    assert selection.xianyu.price == 79
    assert selection.xianyu.canonical_product_key == source.canonical_product_key


def test_structured_source_refuses_one_title_only_resale_card() -> None:
    source = wameiji_observation(
        canonical_product_key="catalog:jaca5480|edition:initial_limited",
        title="嵐 Japonism DVD付初回限定盤 JACA-5480",
        condition_group="complete_used",
    )
    only = XianyuPriceSample(
        catalog_no="嵐 Japonism",
        title="嵐 ARASHI Japonism 初回限定盘 CD+DVD",
        price_cny=79,
        url="https://www.goofish.com/item?id=domestic-only",
        image_url="https://img.alicdn.com/domestic-only.jpg",
        raw_text="国内现货 初回限定盘",
    )

    assert (
        choose_xianyu_pair(
            source,
            [only],
            captured_at="2026-09-09T01:01:00+08:00",
            minimum_title_samples=2,
        )
        is None
    )


def test_structured_source_can_accept_one_strict_reverse_discovery_card() -> None:
    source = wameiji_observation(
        canonical_product_key="jan:4988009093291|edition:limited",
        title="UVERworld 0 CHOIR 初回生産限定盤 CD+DVD",
        condition_group="minor_damage",
    )
    only = XianyuPriceSample(
        catalog_no="UVERworld 0 CHOIR",
        title="UVERworld Ø CHOIR 日版初回限定盘 CD+DVD",
        price_cny=9.9,
        url="https://www.goofish.com/item?id=domestic-only",
        image_url="https://img.alicdn.com/domestic-only.jpg",
        raw_text="国内现货，外纸套和盘面有正常使用划痕，初回限定盘",
    )

    selection = choose_xianyu_pair(
        source,
        [only],
        captured_at="2026-09-09T01:01:00+08:00",
        minimum_title_samples=1,
    )

    assert selection is not None
    assert selection.match_kind == "strict_title_single"
    assert selection.matched_listing_count == 1
    assert selection.xianyu.price == 9.9
    assert selection.xianyu.canonical_product_key == source.canonical_product_key


def test_structured_pair_prefers_exact_identifier_over_cheaper_title_only_card() -> None:
    source = wameiji_observation(
        canonical_product_key="catalog:srcl3023",
        title="TUBE Melodies & Memories CD SRCL-3023",
        condition_group="complete_used",
    )
    exact = XianyuPriceSample(
        catalog_no="SRCL-3023",
        title="TUBE Melodies & Memories 日版CD SRCL-3023",
        price_cny=25,
        url="https://www.goofish.com/item?id=exact-code",
        image_url="https://img.alicdn.com/exact-code.jpg",
        raw_text="盘面干净，播放正常",
    )
    ambiguous_format = XianyuPriceSample(
        catalog_no=None,
        title="TUBE Melodies & Memories 日版8cm CD三寸碟",
        price_cny=10,
        url="https://www.goofish.com/item?id=title-only",
        image_url="https://img.alicdn.com/title-only.jpg",
        raw_text="盘面干净，播放正常",
    )

    selection = choose_xianyu_pair(
        source,
        [ambiguous_format, exact],
        captured_at="2026-09-09T07:12:00+08:00",
        minimum_title_samples=1,
        required_variant_title=exact.title,
        required_media_type="cd",
    )

    assert selection is not None
    assert selection.match_kind == "structured_identifier"
    assert selection.xianyu.source_listing_id == "exact-code"
    assert selection.xianyu.price == 25


def test_structured_source_rejects_same_album_from_a_different_market_region() -> None:
    source = wameiji_observation(
        canonical_product_key="jan:4988006190931|condition:minor_damage",
        title="Utada Hikaru SINGLE COLLECTION VOL.1 CD TOCT-25300",
        condition_group="minor_damage",
    )


def test_cd_pair_rejects_same_english_words_on_a_book_listing() -> None:
    source = wameiji_observation(
        canonical_product_key="jan:4988006197893",
        title="THE REMAINS / ストレイテナー CD",
        condition_group="complete_used",
    )
    book = XianyuPriceSample(
        catalog_no="ストレイテナー The Remains",
        title="The Remains of the Day 英文实体书 长日将尽",
        price_cny=18,
        url="https://www.goofish.com/item?id=english-book",
        image_url="https://img.alicdn.com/english-book.jpg",
        raw_text="国内印刷，全新书籍，大量库存",
    )

    assert (
        choose_xianyu_pair(
            source,
            [book],
            captured_at="2026-09-09T04:55:00+08:00",
            minimum_title_samples=1,
            required_variant_title="ストレイテナー The Remains CD",
            required_media_type="cd",
        )
        is None
    )
    mainland_pressing = XianyuPriceSample(
        catalog_no="日版 CD 品番",
        title="宇多田光 Single Collection Vol.1 内地引进版 CD",
        price_cny=69,
        url="https://www.goofish.com/item?id=mainland-pressing",
        image_url="https://img.alicdn.com/mainland-pressing.jpg",
        raw_text="内地引进版，外壳与侧标磨损，播放正常",
    )

    assert (
        choose_xianyu_pair(
            source,
            [mainland_pressing],
            captured_at="2026-09-09T04:50:00+08:00",
            minimum_title_samples=1,
            required_variant_title="宇多田光 Single Collection Vol.1 日版 CD",
        )
        is None
    )


def test_reverse_pair_rejects_a_different_release_from_the_same_franchise() -> None:
    source = wameiji_observation(
        canonical_product_key="jan:4540774147847",
        title=(
            "【中古】 アイドルマスター シャイニーカラーズ "
            "BRILLI @ NT WING 04 夢咲き After school "
            "放課後クライマックスガールズ [CD]"
        ),
        price=295,
        condition_group="complete_used",
    )
    different_release = XianyuPriceSample(
        catalog_no=None,
        title=(
            "偶像大师CD LET'S GO HAPPY!! THE IDOLM@STER CINDERELLA GIRLS "
            "ANIMATION PROJECT 05 实拍图带侧标"
        ),
        price_cny=15,
        url="https://www.goofish.com/item?id=different-release",
        image_url="https://img.alicdn.com/different-release.jpg",
        raw_text="音像产品，完整CD",
    )

    assert (
        choose_xianyu_pair(
            source,
            [different_release],
            captured_at="2026-09-09T01:01:00+08:00",
            minimum_title_samples=1,
            required_variant_title=(
                "带侧 THE IDOLM STER SHINY C OLORS BRILLI NT WING 04 "
                "偶像大师闪耀色彩 放"
            ),
            required_media_type="cd",
        )
        is None
    )


def test_unspecified_import_pressing_cannot_match_an_explicit_us_first_pressing() -> None:
    source = wameiji_observation(
        canonical_product_key="title:v1:chaka-khan",
        title="CHAKA KHAN / I FEEL FOR YOU 輸入盤 [CD]",
        price=422,
        condition_group="complete_used",
    )
    us_first_pressing = XianyuPriceSample(
        catalog_no=None,
        title="Chaka Khan I Feel For You CD 1984美国首版 满银满标靶盘",
        price_cny=30,
        url="https://www.goofish.com/item?id=us-first-pressing",
        image_url="https://img.alicdn.com/us-first-pressing.jpg",
        raw_text="美国首版，完整CD",
    )

    assert (
        choose_xianyu_pair(
            source,
            [us_first_pressing],
            captured_at="2026-09-09T01:01:00+08:00",
            minimum_title_samples=1,
            required_variant_title="Chaka Khan I Feel For You",
            required_media_type="cd",
        )
        is None
    )


def test_import_pressing_cannot_match_an_explicit_japanese_first_pressing() -> None:
    source = wameiji_observation(
        canonical_product_key="title:v1:chaka-khan",
        title="CHAKA KHAN / I FEEL FOR YOU 輸入盤 [CD]",
        price=422,
        condition_group="complete_used",
    )
    japanese_first_pressing = XianyuPriceSample(
        catalog_no=None,
        title="Chaka Khan I Feel For You 日首罕见CD",
        price_cny=120,
        url="https://www.goofish.com/item?id=japanese-first-pressing",
        image_url="https://img.alicdn.com/japanese-first-pressing.jpg",
        raw_text="日本首版，完整CD",
    )

    assert (
        choose_xianyu_pair(
            source,
            [japanese_first_pressing],
            captured_at="2026-09-09T01:01:00+08:00",
            minimum_title_samples=1,
            required_variant_title="Chaka Khan I Feel For You",
            required_media_type="cd",
        )
        is None
    )


def test_reverse_seed_title_can_bridge_different_marketplace_title_formatting() -> None:
    source = wameiji_observation(
        canonical_product_key="jan:4988005376657|edition:initial_limited",
        title=(
            "【中古】ロックの逆襲-スーパースターの条件 初回限定盤A "
            "DVD付 CD 雅-miyavi- MYV / ユニバーサルJ"
        ),
        condition_group="complete_used",
    )
    origin_title = (
        "雅-miyavi ロックの逆襲 限定盘A CD+DVD 日版 初回限定 "
        "视觉系摇滚 石原贵雅 经典 摇滚的逆袭"
    )
    origin = XianyuPriceSample(
        catalog_no="日版 CD 品番",
        title=origin_title,
        price_cny=19,
        url="https://www.goofish.com/item?id=miyavi-origin",
        image_url="https://img.alicdn.com/miyavi-origin.jpg",
        raw_text="二手现货，原盒与CD、DVD齐全",
    )

    selection = choose_xianyu_pair(
        source,
        [origin],
        captured_at="2026-09-09T04:15:00+08:00",
        minimum_title_samples=1,
        required_variant_title=origin_title + " 初回限定版A",
    )

    assert selection is not None
    assert selection.xianyu.source_listing_id == "miyavi-origin"


def test_title_pair_requires_two_distinct_strict_matches() -> None:
    source = wameiji_observation(
        canonical_product_key=None,
        title="SW版 ラジルギ2 限定版 Switch",
        condition_group="complete_used",
    )
    samples = [
        XianyuPriceSample(
            catalog_no="Switch ラジルギ2",
            title="ラジルギ2 NS 限定版",
            price_cny=210,
            url="https://www.goofish.com/item?id=x-1",
            image_url="https://img.alicdn.com/x-1.jpg",
            raw_text="二手",
        ),
        XianyuPriceSample(
            catalog_no="Switch ラジルギ2",
            title="Switch ラジルギ2 限定版",
            price_cny=180,
            url="https://www.goofish.com/item?id=x-2",
            image_url="https://img.alicdn.com/x-2.jpg",
            raw_text="二手",
        ),
    ]

    selection = choose_xianyu_pair(
        source,
        samples,
        captured_at="2026-09-09T01:01:00+08:00",
        minimum_title_samples=2,
    )

    assert selection is not None
    assert selection.match_kind == "strict_title"
    assert selection.matched_listing_count == 2
    assert selection.xianyu.price == 180
    assert selection.wameiji.canonical_product_key.startswith("title:")
    assert selection.xianyu.canonical_product_key == selection.wameiji.canonical_product_key


def test_title_pair_refuses_one_listing_even_when_its_title_matches() -> None:
    source = wameiji_observation(
        canonical_product_key=None,
        title="SW版 ラジルギ2 限定版 Switch",
        condition_group="complete_used",
    )
    only = XianyuPriceSample(
        catalog_no="Switch ラジルギ2",
        title="ラジルギ2 NS 限定版",
        price_cny=180,
        url="https://www.goofish.com/item?id=x-1",
        image_url="https://img.alicdn.com/x-1.jpg",
        raw_text="二手",
    )

    assert (
        choose_xianyu_pair(
            source,
            [only],
            captured_at="2026-09-09T01:01:00+08:00",
            minimum_title_samples=2,
        )
        is None
    )


def test_structured_seed_accepts_a_wameiji_card_whose_title_omits_identifier() -> None:
    item = MarketItem(
        source="wameiji",
        title="DIABOLIK LOVERS LUNATIC PARADE 限定版 PS Vita",
        price=3267,
        currency="JPY",
        url="https://meruki.cn/mall/yahoo/detail/example",
        raw_text="DIABOLIK LOVERS LUNATIC PARADE 限定版 PS Vita",
    )

    assert wameiji_item_matches_seed(
        item,
        seed_title="DIABOLIK LOVERS LUNATIC PARADE 限定版 PS Vitaゲームソフト",
        query="4995857094189",
        query_kind="structured_identifier",
    )


def test_structured_seed_rejects_an_unrelated_card_without_identifier() -> None:
    item = MarketItem(
        source="wameiji",
        title="DIABOLIK LOVERS DARK FATE 限定版 PS Vita",
        price=100,
        currency="JPY",
        url="https://meruki.cn/mall/yahoo/detail/wrong",
        raw_text="DIABOLIK LOVERS DARK FATE 限定版 PS Vita",
    )

    assert not wameiji_item_matches_seed(
        item,
        seed_title="DIABOLIK LOVERS LUNATIC PARADE 限定版 PS Vitaゲームソフト",
        query="4995857094189",
        query_kind="structured_identifier",
    )


def test_catalog_query_rejects_an_unrelated_product_with_the_same_code() -> None:
    item = MarketItem(
        source="wameiji",
        title="ハケ 塗り刷毛 4cm ベジライブ CC-1079",
        price=699,
        currency="JPY",
        catalog_no="CC-1079",
        url="https://meruki.cn/mall/rakuten/detail/brush",
        raw_text="製菓 お好み焼き たこ焼き ソース パール金属",
    )

    assert not wameiji_item_matches_seed(
        item,
        seed_title="进口 马勒第六交响曲 悲剧 CD CC-1079",
        query="CC-1079",
        query_kind="structured_identifier",
        seed_media_type="cd",
    )


def test_catalog_query_accepts_same_physical_media_despite_translated_title() -> None:
    item = MarketItem(
        source="wameiji",
        title="King & Prince L& 通常盤 CD UPCJ-1002",
        price=330,
        currency="JPY",
        catalog_no="UPCJ-1002",
        url="https://meruki.cn/mall/rakuten/detail/upcj-1002",
        raw_text="邦楽 CD 中古品",
    )

    assert wameiji_item_matches_seed(
        item,
        seed_title="King Prince L 带侧标 特典小卡 通常版",
        query="UPCJ-1002",
        query_kind="structured_identifier",
        seed_media_type="cd",
    )


def test_cd_title_query_rejects_sheet_music_with_the_same_song_title() -> None:
    item = MarketItem(
        source="wameiji",
        title=(
            "【中古】 Ｐミニアルバム Ｋｉｒｏｒｏ 僕らのメッセージ／"
            "ずっと忘れな／ヤマハミュージックメディア"
        ),
        price=220,
        currency="JPY",
        url="https://meruki.cn/mall/rakuten/detail/sheet-music",
        raw_text=(
            "商品仕様タイトルPミニアルバム Kiroro 僕らのメッセージ "
            "出版社ヤマハミュージックメディア JAN 9784636259124"
        ),
    )

    assert not wameiji_item_matches_seed(
        item,
        seed_title="KIRORO 僕らのメッセージ 日版原版CD",
        query="KIRORO 僕らのメッセージ",
        query_kind="strict_title",
        seed_media_type="cd",
    )


def test_title_query_drops_wameiji_price_and_package_metadata() -> None:
    title = (
        "ＵＶＥＲｗｏｒｌｄ/０ＣＨＯＩＲ 初回生産盤 DVDスリーブケース付 "
        "定価￥3,700-(税抜)) ミニフォトブック付 セル版 ④"
    )

    assert title_query_without_identifiers(title) == "UVERworld 0 CHOIR"


def test_reverse_discovery_builds_short_product_query_from_domestic_cd_card() -> None:
    sample = xianyu_sample(
        "reverse-cd",
        title=(
            "UVERworld Ø CHOIR 日版初回限定盘 CD+DVD 日本摇滚乐队专辑，"
            "外纸套存在正常使用痕迹，盘片可正常播放，包邮"
        ),
        price=19.9,
        raw_text="国内现货 二手日版音像 实物如图",
    )

    screened = build_reverse_discovery_candidate(sample)

    assert screened.rejection_reason is None
    assert screened.candidate is not None
    assert screened.candidate.search_title == "UVERworld 0 CHOIR"
    assert screened.candidate.seed_title == "UVERworld 0 CHOIR 初回限定版"
    assert screened.candidate.catalog_no is None
    assert screened.candidate.media_type == "cd"


def test_reverse_discovery_does_not_treat_a_quoted_track_after_shoulu_as_album_title() -> None:
    sample = xianyu_sample(
        "reverse-tracklist-quote",
        title=(
            "original love : eyes 日版CD，1993年东芝EMI TOCT-8037，"
            "带原装CD盒。收录《LET'S GO!》《サンシャインロマンス》"
        ),
        price=10,
        raw_text="碟片保存很好，播放正常",
    )

    screened = build_reverse_discovery_candidate(sample)

    assert screened.candidate is not None
    assert screened.candidate.catalog_no == "TOCT-8037"
    assert screened.candidate.search_title == "original love eyes"


def test_reverse_discovery_retains_explicit_hyphenated_catalog_number() -> None:
    sample = xianyu_sample(
        "reverse-code",
        title=(
            "全新未拆封日版初回限定盘CD+DVD，w-inds.精选辑"
            "《Single Collection BEST ELEVEN》，编号PCCA-02617。"
        ),
        price=19,
        raw_text="国内现货",
    )

    screened = build_reverse_discovery_candidate(sample)

    assert screened.rejection_reason is None
    assert screened.candidate is not None
    assert screened.candidate.search_title == "w inds Single Collection BEST ELEVEN"
    assert screened.candidate.catalog_no == "PCCA-02617"


def test_reverse_discovery_normalizes_spaced_catalog_number() -> None:
    sample = xianyu_sample(
        "reverse-spaced-code",
        title=(
            "SPEED Carry On my way 日版CD，品番 TFCC 88142，"
            "原盒与歌词本齐全，盘面微划，播放正常"
        ),
        price=12,
        raw_text="二手国内现货",
    )

    screened = build_reverse_discovery_candidate(sample)

    assert screened.candidate is not None
    assert screened.candidate.catalog_no == "TFCC-88142"


def test_reverse_discovery_prefers_labeled_code_over_idol_group_name() -> None:
    sample = xianyu_sample(
        "reverse-idol-code",
        title=(
            "SKE48 恋落ちフラグ 日版单曲CD，"
            "品番 AVCD‑84975，原盒与侧标齐全"
        ),
        price=28.88,
        raw_text="全新未拆封国内现货",
    )

    screened = build_reverse_discovery_candidate(sample)

    assert screened.candidate is not None
    assert screened.candidate.catalog_no == "AVCD-84975"


def test_reverse_discovery_preserves_game_platform_in_lookup_title() -> None:
    sample = xianyu_sample(
        "reverse-game",
        title="Switch 日版 异度神剑3 限定版 实体卡带，盒说齐全",
        price=299,
        raw_text="二手现货",
    )

    screened = build_reverse_discovery_candidate(sample)

    assert screened.rejection_reason is None
    assert screened.candidate is not None
    assert screened.candidate.search_title == "Switch 异度神剑3"
    assert screened.candidate.seed_title == "Switch 异度神剑3 限定版"
    assert screened.candidate.media_type == "physical_game"


def test_reverse_discovery_recognizes_cd_joined_to_chinese_text() -> None:
    sample = xianyu_sample(
        "reverse-joined-cd",
        title="NEWS《color》日版CD，初回限定盘，收录14首，播放正常",
        price=18,
        raw_text="国内现货",
    )

    screened = build_reverse_discovery_candidate(sample)

    assert screened.rejection_reason is None
    assert screened.candidate is not None
    assert screened.candidate.search_title == "NEWS color"
    assert screened.candidate.media_type == "cd"


def test_reverse_discovery_does_not_turn_cd_release_year_into_catalog_number() -> None:
    sample = xianyu_sample(
        "reverse-year",
        title="中岛美嘉 TEARS+DEARS 2CD+DVD 日版原版 2003年发行",
        price=300,
        raw_text="已拆封，盘面干净，配件齐全",
    )

    screened = build_reverse_discovery_candidate(sample)

    assert screened.candidate is not None
    assert screened.candidate.catalog_no is None
    assert screened.candidate.search_title == "中岛美嘉 TEARS DEARS"


def test_reverse_discovery_rejects_a_dvd_only_music_listing() -> None:
    sample = xianyu_sample(
        "reverse-dvd-only",
        title="BoA Outgrow 2006年日版DVD裸碟一张，碟面保存很好",
        price=8,
    )

    screened = build_reverse_discovery_candidate(sample)

    assert screened.candidate is None
    assert screened.rejection_reason == "not_supported_physical_media"


def test_reverse_discovery_rejects_a_cassette_listing_even_when_page_text_mentions_cd() -> None:
    sample = xianyu_sample(
        "reverse-cassette",
        title="Elton John Love Songs 正版磁带，有歌词原盒",
        price=10,
        raw_text="搜索页相关推荐：日版 CD TOCT-8037",
    )

    screened = build_reverse_discovery_candidate(sample)

    assert screened.candidate is None
    assert screened.rejection_reason == "not_supported_physical_media"


def test_reverse_discovery_rejects_compound_price_such_as_three_discs_for_thirty() -> None:
    sample = xianyu_sample(
        "reverse-three-for-thirty",
        title="布袋寅泰 King & Queen 日版CD 三张30不包邮",
        price=30,
    )

    screened = build_reverse_discovery_candidate(sample)

    assert screened.candidate is None
    assert screened.rejection_reason == "ambiguous_multi_item_price"


def test_reverse_discovery_rejects_missing_disc_before_any_lookup() -> None:
    sample = xianyu_sample(
        "reverse-no-disc",
        title="YUI CAN'T BUY MY LOVE 日版初回限定 注意没有CD碟，只卖歌词本盒子和DVD",
        price=16,
    )

    screened = build_reverse_discovery_candidate(sample)

    assert screened.candidate is None
    assert screened.rejection_reason == "incomplete_product"


def test_reverse_discovery_rejects_proxy_and_ambiguous_multi_item_cards() -> None:
    proxy = xianyu_sample(
        "reverse-proxy",
        title="Oasis Whatever 日版初回限定 CD 日本代购",
        raw_text="日本直邮，国际运费后补",
    )
    multi = xianyu_sample(
        "reverse-multi",
        title="YUI 日版CD多款可选，价格分别是180、150、100、50、60",
        price=10,
    )

    proxy_screened = build_reverse_discovery_candidate(proxy)
    multi_screened = build_reverse_discovery_candidate(multi)

    assert proxy_screened.rejection_reason == "cross_border_proxy_quote"
    assert multi_screened.rejection_reason == "ambiguous_multi_item_price"


def test_reverse_discovery_rejects_vague_artist_only_card() -> None:
    sample = xianyu_sample(
        "reverse-vague",
        title="岚 ARASHI 初回限定盘 CD+DVD 日版全新未拆封，通常盘打包可优惠",
        price=29,
    )

    screened = build_reverse_discovery_candidate(sample)

    assert screened.candidate is None
    assert screened.rejection_reason == "no_distinct_product_title"
