from cd_monitor.core.models import XianyuPriceSample
from cd_monitor.core.xianyu_cleaner import clean_xianyu_samples, estimate_xianyu_price


def sample(title: str, price: float) -> XianyuPriceSample:
    return XianyuPriceSample(catalog_no="SRCL-3520", title=title, price_cny=price, raw_text=title)


def test_filters_extreme_and_noise_samples() -> None:
    samples = [
        sample("SRCL-3520 正常", 100),
        sample("SRCL-3520 1元展示", 1),
        sample("SRCL-3520 天价", 9999),
        sample("收 SRCL-3520", 80),
        sample("求 SRCL-3520", 80),
        sample("代拍 SRCL-3520", 80),
        sample("SRCL-3520 仅展示", 80),
    ]
    cleaned = clean_xianyu_samples(samples)
    assert len([s for s in cleaned if s.is_valid]) == 1
    assert {s.invalid_reason for s in cleaned if not s.is_valid} >= {
        "invalid_extreme_low",
        "invalid_extreme_high",
        "invalid_noise",
    }


def test_keeps_listing_that_says_tracks_are_included_or_collectible() -> None:
    cleaned = clean_xianyu_samples(
        [sample("Xenoblade 原声CD，收录12首曲目，适合收藏", 160)]
    )

    assert cleaned[0].is_valid
    assert cleaned[0].invalid_reason is None


def test_filters_crossborder_proxy_listing_out_of_resale_reference() -> None:
    cleaned = clean_xianyu_samples(
        [
            sample(
                "PS Vita 领结×恶意 -无限版- VLJM-38101 日本代购 ",
                118,
            )
        ]
    )

    assert not cleaned[0].is_valid
    assert cleaned[0].invalid_reason == "invalid_proxy_listing"


def test_game_bundle_keeps_accessories_but_rejects_a_single_bonus_item() -> None:
    cleaned = clean_xianyu_samples(
        [
            sample(
                "AQUARIUM 完全生产限定版 Switch 游戏卡带 特典CD 立牌 挂件 "
                "售出不退不换",
                450,
            ),
            sample("AQUARIUM 特典单出 立牌", 80),
        ],
        allow_complete_game_bundles=True,
    )

    assert cleaned[0].is_valid is True
    assert cleaned[1].is_valid is False
    assert cleaned[1].invalid_reason == "invalid_noise"


def test_new_source_reference_excludes_explicitly_used_resale_cards() -> None:
    cleaned = clean_xianyu_samples(
        [
            sample("Switch Macross Shooting Insight 限定版 日版二手", 245),
            sample("Switch Macross Shooting Insight 限定版 全新未拆封", 340),
        ],
        require_new_condition=True,
    )

    assert cleaned[0].is_valid is False
    assert cleaned[0].invalid_reason == "invalid_condition_mismatch"
    assert cleaned[1].is_valid is True


def test_reference_price_uses_trimmed_mean_for_six_or_more() -> None:
    estimate = estimate_xianyu_price([sample("SRCL-3520", p) for p in [50, 80, 90, 100, 110, 300]])
    assert estimate.valid_sample_count == 6
    assert estimate.reference_price_cny == 95
    assert estimate.liquidity_status == "normal"


def test_reference_price_uses_median_for_three_to_five() -> None:
    estimate = estimate_xianyu_price([sample("SRCL-3520", p) for p in [80, 100, 130]])
    assert estimate.reference_price_cny == 100
    assert estimate.liquidity_status == "thin"


def test_reference_price_marks_poor_under_three() -> None:
    estimate = estimate_xianyu_price([sample("SRCL-3520", 100), sample("SRCL-3520", 120)])
    assert estimate.reference_price_cny == 0
    assert estimate.liquidity_status == "poor"


def test_reference_price_uses_configured_two_sample_threshold() -> None:
    estimate = estimate_xianyu_price(
        [sample("SRCL-3520", 100), sample("SRCL-3520", 120)],
        min_reference_samples=2,
    )

    assert estimate.reference_price_cny == 110
    assert estimate.liquidity_status == "thin"


def test_filters_xianyu_samples_by_expected_edition() -> None:
    samples = [
        sample("Artist SRCL-3520 初回限定 帯付き", 260),
        sample("Artist SRCL-3520 初回限定", 280),
        sample("Artist SRCL-3520 通常盤", 120),
        sample("Artist SRCL-3520 初回限定 未開封", 300),
    ]

    estimate = estimate_xianyu_price(samples, edition="初回限定")

    assert estimate.valid_sample_count == 3
    assert estimate.reference_price_cny == 280
    assert estimate.invalid_samples[0].invalid_reason == "invalid_edition_mismatch"


def test_reference_price_respects_sample_limit() -> None:
    estimate = estimate_xianyu_price(
        [sample("SRCL-3520", p) for p in [50, 80, 90, 100, 110, 300]],
        sample_limit=3,
    )

    assert estimate.valid_sample_count == 3
    assert estimate.reference_price_cny == 80
