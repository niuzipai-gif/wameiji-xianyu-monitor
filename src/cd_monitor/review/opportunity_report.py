from __future__ import annotations

from cd_monitor.core.models import Opportunity


def render_opportunity_report(
    opportunities: list[Opportunity],
    review_decisions: list[dict[str, object]] | None = None,
    candidate_rechecks: list[dict[str, object]] | None = None,
    opportunity_ids: list[int] | None = None,
) -> str:
    decisions_by_catalog = _group_review_decisions(
        opportunities,
        review_decisions or [],
        opportunity_ids,
    )
    rechecks_by_catalog = _group_review_decisions(
        opportunities,
        candidate_rechecks or [],
        opportunity_ids,
    )
    lines = ["# CD 价差人工复核报告", ""]
    for index, opp in enumerate(opportunities):
        lines.extend(
            [
                f"## {opp.catalog_no} / {opp.decision}",
                _opportunity_id_line(opportunity_ids, index),
                f"- 日本侧标题: {opp.item.title}",
                f"- 日本侧来源: {opp.item.source_site or opp.item.source}",
                f"- 日本侧价格: {opp.item.price} {opp.item.currency}",
                f"- 日本侧链接: {opp.item.url or '无'}",
                f"- 图片 URL: {opp.item.image_url or '无'}",
                f"- 可购买状态: {opp.item.availability}",
                f"- 品相片段: {opp.item.condition_text or '无'}",
                f"- 落地成本: {opp.landed_cost}",
                f"- 闲鱼参考价: {opp.xianyu_reference_price}",
                f"- 预估成交价: {opp.expected_sale_price}",
                f"- 预估收入: {opp.expected_revenue}",
                f"- 预估利润: {opp.expected_profit}",
                f"- 净利润率: {opp.net_margin}",
                f"- 周转 ROI: {opp.turnover_adjusted_roi}",
                f"- 有效闲鱼样本数: {opp.valid_xianyu_sample_count}",
                f"- 流动性: {opp.liquidity_status}",
                f"- 匹配置信度: {opp.match_confidence}",
                f"- 风险标签: {', '.join(opp.risk_labels) or '无'}",
                f"- 人工复核建议: {opp.review_advice}",
                "- 复核字段: accepted_for_personal_collection / rejected_version_mismatch / "
                "rejected_xianyu_noise / rejected_low_profit / rejected_sold_out / "
                "rejected_condition_bad / rejected_liquidity_poor / rejected_other",
            ]
        )
        for decision in decisions_by_catalog.get(opp.catalog_no, []):
            lines.append(f"- 已记录复核: {decision.get('result')} / {decision.get('note') or ''}")
        for recheck in rechecks_by_catalog.get(opp.catalog_no, []):
            lines.append(
                f"- 已记录二次复核: {recheck.get('status')} / {recheck.get('reason') or ''}"
            )
        lines.append("")
    return "\n".join(lines)


def _group_review_decisions(
    opportunities: list[Opportunity],
    review_decisions: list[dict[str, object]],
    opportunity_ids: list[int] | None = None,
) -> dict[str, list[dict[str, object]]]:
    if opportunity_ids is None:
        catalog_by_id = {index + 1: opp.catalog_no for index, opp in enumerate(opportunities)}
    else:
        catalog_by_id = {
            opportunity_id: opp.catalog_no for opportunity_id, opp in zip(opportunity_ids, opportunities)
        }
    grouped: dict[str, list[dict[str, object]]] = {}
    for decision in review_decisions:
        opportunity_id = decision.get("opportunity_id")
        if not isinstance(opportunity_id, int):
            continue
        catalog_no = catalog_by_id.get(opportunity_id)
        if catalog_no:
            grouped.setdefault(catalog_no, []).append(decision)
    return grouped


def _opportunity_id_line(opportunity_ids: list[int] | None, index: int) -> str:
    if opportunity_ids is None or index >= len(opportunity_ids):
        return "- Opportunity ID: 未提供"
    return f"- Opportunity ID: {opportunity_ids[index]}"
