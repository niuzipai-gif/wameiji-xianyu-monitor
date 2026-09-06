from cd_monitor.core.models import MarketItem, Opportunity
from cd_monitor.review.opportunity_report import render_opportunity_report


def test_report_groups_review_and_recheck_by_real_opportunity_id() -> None:
    newer = _opportunity("KSCL-9999")
    older = _opportunity("SRCL-3520")

    report = render_opportunity_report(
        [newer, older],
        review_decisions=[
            {"opportunity_id": 10, "result": "rejected_other", "note": "belongs to KSCL"},
            {"opportunity_id": 3, "result": "rejected_low_profit", "note": "belongs to SRCL"},
        ],
        candidate_rechecks=[
            {"opportunity_id": 10, "status": "confirmed", "reason": "KSCL confirmed"},
            {"opportunity_id": 3, "status": "rejected", "reason": "SRCL rejected"},
        ],
        opportunity_ids=[10, 3],
    )

    kscl_section = report.split("## KSCL-9999 / strong_alert", 1)[1].split("## SRCL-3520", 1)[0]
    srcl_section = report.split("## SRCL-3520 / strong_alert", 1)[1]
    assert "belongs to KSCL" in kscl_section
    assert "KSCL confirmed" in kscl_section
    assert "belongs to SRCL" not in kscl_section
    assert "SRCL rejected" in srcl_section
    assert "KSCL confirmed" not in srcl_section


def test_report_includes_review_context_fields() -> None:
    opportunity = Opportunity(
        catalog_no="SRCL-3520",
        item=MarketItem(
            source="wameiji",
            source_site="mercari",
            title="Artist SRCL-3520 初回限定 帯付き",
            price=1200,
            currency="JPY",
            url="https://example.invalid/wameiji/srcl-3520",
            image_url="https://example.invalid/cover.jpg",
            availability="available",
            condition_text="帯付き 良品",
        ),
        xianyu_reference_price=280,
        expected_sale_price=240,
        landed_cost=120,
        expected_revenue=225,
        expected_profit=105,
        net_margin=0.875,
        turnover_adjusted_roi=0.875,
        match_confidence=0.93,
        valid_xianyu_sample_count=4,
        liquidity_status="normal",
        decision="strong_alert",
        risk_labels=[],
    )

    report = render_opportunity_report([opportunity], opportunity_ids=[7])

    assert "- Opportunity ID: 7" in report
    assert "- 日本侧来源: mercari" in report
    assert "- 日本侧链接: https://example.invalid/wameiji/srcl-3520" in report
    assert "- 图片 URL: https://example.invalid/cover.jpg" in report
    assert "- 可购买状态: available" in report
    assert "- 品相片段: 帯付き 良品" in report
    assert "- 预估成交价: 240" in report
    assert "- 预估收入: 225" in report
    assert "- 净利润率: 0.875" in report
    assert "- 周转 ROI: 0.875" in report
    assert "- 有效闲鱼样本数: 4" in report
    assert "- 流动性: normal" in report
    assert "- 人工复核建议: 人工复核后再决定是否采购" in report


def _opportunity(catalog_no: str) -> Opportunity:
    return Opportunity(
        catalog_no=catalog_no,
        item=MarketItem(source="wameiji", title=f"{catalog_no} title", price=1000),
        xianyu_reference_price=200,
        expected_sale_price=180,
        landed_cost=100,
        expected_revenue=170,
        expected_profit=70,
        net_margin=0.7,
        turnover_adjusted_roi=0.7,
        match_confidence=0.95,
        valid_xianyu_sample_count=3,
        liquidity_status="normal",
        decision="strong_alert",
    )
