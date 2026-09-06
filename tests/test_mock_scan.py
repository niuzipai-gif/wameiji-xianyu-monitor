from cd_monitor.core.evaluator import evaluate_opportunity
from cd_monitor.core.matcher import compute_match_confidence
from cd_monitor.core.cost_model import compute_landed_cost
from cd_monitor.core.xianyu_cleaner import estimate_xianyu_price
from cd_monitor.sources.mock import MockWameijiAdapter, MockXianyuAdapter


def test_mock_adapters_feed_opportunity_pipeline(tmp_path) -> None:
    wameiji_path = tmp_path / "wameiji.json"
    xianyu_path = tmp_path / "xianyu.json"
    wameiji_path.write_text(
        '[{"source":"wameiji","source_site":"mercari","external_item_id":"w1",'
        '"title":"Artist SRCL-3520 初回限定","price":1200,"currency":"JPY"}]',
        encoding="utf-8",
    )
    xianyu_path.write_text(
        '[{"catalog_no":"SRCL-3520","title":"Artist SRCL-3520 初回限定","price_cny":260},'
        '{"catalog_no":"SRCL-3520","title":"Artist SRCL-3520 初回限定","price_cny":280},'
        '{"catalog_no":"SRCL-3520","title":"Artist SRCL-3520 初回限定","price_cny":300}]',
        encoding="utf-8",
    )
    watch = MockWameijiAdapter(wameiji_path).watch_item("SRCL-3520")
    item = MockWameijiAdapter(wameiji_path).search(watch)[0]
    samples = MockXianyuAdapter(xianyu_path).search_samples(watch)
    opp = evaluate_opportunity(
        watch,
        item,
        compute_match_confidence(item, watch),
        estimate_xianyu_price(samples),
        compute_landed_cost(item),
    )
    assert opp.catalog_no == "SRCL-3520"
    assert opp.decision in {"strong_alert", "weak_alert", "review_only", "reject"}
