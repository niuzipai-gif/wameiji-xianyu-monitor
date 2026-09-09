from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import asyncio

from cd_monitor.core.models import XianyuPriceSample
from cd_monitor.core.dual_market import ListingObservation


def _load_script_module():
    script_path = Path("scripts/collect_dual_market_batch.py")
    spec = importlib.util.spec_from_file_location("collect_dual_market_batch_cli", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sample(listing_id: str, *, price: float) -> XianyuPriceSample:
    return XianyuPriceSample(
        catalog_no="日版 CD 初回限定盘",
        title="UVERworld Ø CHOIR 日版初回限定盘 CD+DVD，盘片可播放",
        price_cny=price,
        url=f"https://www.goofish.com/item?id={listing_id}",
        image_url=f"https://img.alicdn.com/{listing_id}.webp",
        raw_text="国内现货 二手日版音像",
    )


def test_reverse_samples_become_deduplicated_lowest_price_seeds() -> None:
    module = _load_script_module()
    higher = _sample("higher", price=29)
    lower = _sample("lower", price=19)

    seeds, screenings = module.build_reverse_seeds(
        [higher, lower],
        max_candidates=10,
        evidence_by_url={
            str(lower.url): {
                "snapshot_path": "runs/xianyu-broad.html",
                "screenshot_path": "runs/xianyu-broad.png",
            }
        },
    )

    assert len(seeds) == 1
    assert seeds[0].title == "UVERworld 0 CHOIR 初回限定版"
    assert seeds[0].lookup_query == "UVERworld 0 CHOIR"
    assert seeds[0].source_url.endswith("id=lower")
    assert seeds[0].source_price == 19
    assert seeds[0].source_currency == "CNY"
    assert seeds[0].pipeline_stage == "reverse_discovered"
    assert seeds[0].origin_xianyu_sample is lower
    assert seeds[0].origin_snapshot_path == "runs/xianyu-broad.html"
    assert seeds[0].origin_screenshot_path == "runs/xianyu-broad.png"
    assert len(screenings) == 2
    assert sum(row["accepted"] for row in screenings) == 2


def test_reverse_samples_report_rejection_reasons_without_creating_seed() -> None:
    module = _load_script_module()
    incomplete = XianyuPriceSample(
        catalog_no="日版 CD 初回限定盘",
        title="YUI 日版初回限定 注意没有CD碟，只卖歌词本和盒子",
        price_cny=16,
        url="https://www.goofish.com/item?id=no-disc",
        image_url="https://img.alicdn.com/no-disc.webp",
    )

    seeds, screenings = module.build_reverse_seeds([incomplete], max_candidates=10)

    assert seeds == []
    assert screenings == [
        {
            "accepted": False,
            "reason": "incomplete_product",
            "title": incomplete.title,
            "price_cny": 16,
            "url": incomplete.url,
        }
    ]


def test_cli_accepts_live_and_saved_reverse_discovery_sources() -> None:
    module = _load_script_module()

    args = module.parse_args(
        [
            "--seed-db",
            "seed.db",
            "--target-db",
            "target.db",
            "--wameiji-profile",
            "w-profile",
            "--xianyu-profile",
            "x-profile",
            "--run-root",
            "runs",
            "--reverse-query",
            "日版 CD 初回限定盘",
            "--reverse-source",
            "日版 OST CD=saved.html",
            "--reverse-max-candidates",
            "7",
            "--reverse-minimum-title-samples",
            "1",
            "--reverse-scroll-rounds",
            "4",
        ]
    )

    assert args.reverse_query == ["日版 CD 初回限定盘"]
    assert args.reverse_source == ["日版 OST CD=saved.html"]
    assert args.reverse_max_candidates == 7
    assert args.reverse_minimum_title_samples == 1
    assert args.reverse_scroll_rounds == 4


def test_reverse_seed_uses_single_strict_sample_gate_but_forward_seed_keeps_two() -> None:
    module = _load_script_module()
    args = SimpleNamespace(
        minimum_title_samples=2,
        reverse_minimum_title_samples=1,
    )
    reverse = SimpleNamespace(pipeline_stage="reverse_discovered")
    forward = SimpleNamespace(pipeline_stage="resale_queued")

    assert module.minimum_title_samples_for_seed(reverse, args) == 1
    assert module.minimum_title_samples_for_seed(forward, args) == 2


def test_reverse_origin_card_can_be_selected_with_its_own_snapshot_evidence() -> None:
    module = _load_script_module()
    sample = _sample("origin", price=9.9)
    sample.raw_text = "国内现货，盘面有正常使用划痕，初回限定盘"
    seed = module.build_reverse_seeds(
        [sample],
        max_candidates=1,
        evidence_by_url={
            str(sample.url): {
                "snapshot_path": "runs/broad.html",
                "screenshot_path": "runs/broad.png",
            }
        },
    )[0][0]
    wameiji = ListingObservation(
        source="wameiji",
        source_listing_id="w-origin",
        canonical_product_key=None,
        title=(
            "ＵＶＥＲｗｏｒｌｄ/０ＣＨＯＩＲ 初回生産盤 DVDスリーブケース付 "
            "定価￥3,700-(税抜))"
        ),
        price=500,
        currency="JPY",
        url="https://meruki.cn/mall/paypay/detail/w-origin",
        image_url="https://auctions.c.yimg.jp/w-origin.jpg",
        availability="available",
        condition_group="minor_damage",
        completeness="complete",
        evidence_level="detail_verified",
        captured_at="2026-09-09T02:00:00+08:00",
    )

    selection = module.choose_reverse_origin_pair(
        wameiji,
        seed,
        captured_at="2026-09-09T02:01:00+08:00",
    )

    assert selection is not None
    assert selection.match_kind == "strict_title_single"
    assert selection.xianyu.raw_snapshot_path == "runs/broad.html"
    assert selection.xianyu.screenshot_path == "runs/broad.png"


def test_saved_reverse_source_is_parsed_and_counted_without_browser_capture(tmp_path) -> None:
    module = _load_script_module()
    snapshot = tmp_path / "broad.html"
    snapshot.write_text(
        """
        <a class="feeds-item-wrap--hash" href="https://www.goofish.com/item?id=1">
          <img class="feeds-image--hash" src="https://img.alicdn.com/one.webp" />
          <div class="row1-wrap-title--hash" title="UVERworld CHOIR 日版初回限定盘 CD"></div>
          <div class="row3-wrap-price--hash"><span class="number--hash">19</span></div>
        </a>
        """,
        encoding="utf-8",
    )
    args = SimpleNamespace(
        reverse_source=[f"日版 CD 初回限定盘={snapshot}"],
        reverse_query=[],
        reverse_max_candidates=5,
        xianyu_profile=str(tmp_path / "unused-profile"),
        timeout_seconds=1,
        headless=True,
    )
    counters = module.Counters()

    seeds, stage = asyncio.run(
        module.load_reverse_discovery(args, run_dir=tmp_path / "run", counters=counters)
    )

    assert len(seeds) == 1
    assert counters.reverse_queries == 1
    assert counters.reverse_cards_visible == 1
    assert counters.reverse_cards_accepted == 1
    assert counters.reverse_unique_candidates == 1
    assert stage["status"] == "ok"
    assert stage["sources"][0]["capture_mode"] == "saved_html"
    assert stage["sources"][0]["visible_cards"] == 1


def test_run_uses_reverse_discovery_seeds_instead_of_forward_seed_db(
    tmp_path, monkeypatch
) -> None:
    module = _load_script_module()
    seed_db = tmp_path / "seed.db"
    seed_db.write_bytes(b"placeholder")
    target_db = tmp_path / "target.db"
    w_profile = tmp_path / "w-profile"
    x_profile = tmp_path / "x-profile"
    w_profile.mkdir()
    x_profile.mkdir()
    args = module.parse_args(
        [
            "--seed-db",
            str(seed_db),
            "--target-db",
            str(target_db),
            "--wameiji-profile",
            str(w_profile),
            "--xianyu-profile",
            str(x_profile),
            "--run-root",
            str(tmp_path / "runs"),
            "--reverse-query",
            "日版 CD 初回限定盘",
            "--dry-run",
            "--max-seeds",
            "1",
            "--delay-seconds",
            "0",
        ]
    )
    reverse_seed = module.Seed(
        id=-1,
        media_type="cd",
        title="UVERworld CHOIR 初回限定版",
        catalog_no=None,
        jan=None,
        source_item_id=None,
        source_url="https://www.goofish.com/item?id=1",
        source_price=19,
        source_currency="CNY",
        availability="available",
        detail_verified=False,
        pipeline_stage="reverse_discovered",
        last_seen_at="2026-09-09T02:00:00+08:00",
        lookup_query="UVERworld CHOIR",
    )
    calls: list[str] = []

    async def fake_reverse(*_args, **_kwargs):
        calls.append("reverse")
        return [reverse_seed], {"stage": "reverse_discovery", "status": "ok"}

    async def fake_collect(_seed, *, counters, **_kwargs):
        calls.append("collect")
        counters.exact_pairs += 1
        return {"seed_id": -1, "status": "verified"}

    monkeypatch.setattr(module, "load_reverse_discovery", fake_reverse)
    monkeypatch.setattr(module, "collect_one_seed", fake_collect)
    monkeypatch.setattr(
        module,
        "load_seeds",
        lambda _path: (_ for _ in ()).throw(AssertionError("forward seeds used")),
    )

    result = asyncio.run(module.run(args))

    assert result == 0
    assert calls == ["reverse", "collect"]
    report = next((tmp_path / "runs").glob("*/batch-report.json"))
    payload = __import__("json").loads(report.read_text(encoding="utf-8"))
    assert payload["outcomes"][0]["stage"] == "reverse_discovery"
    assert payload["outcomes"][1]["status"] == "verified"


def test_persisted_pair_uses_the_shared_strict_profit_policy() -> None:
    source = Path("scripts/collect_dual_market_batch.py").read_text(encoding="utf-8")

    assert "strict_profit_cost_config_from_snapshot" in source
    assert "DualMarketCostConfig()" not in source
