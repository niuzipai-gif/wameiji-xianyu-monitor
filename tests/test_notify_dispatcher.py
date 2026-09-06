"""Tests for the live notification dispatcher."""
from __future__ import annotations

from pathlib import Path

import pytest

from cd_monitor.config import NotifyConfig, ProjectConfig
from cd_monitor.core.models import Opportunity, MarketItem, WatchItem
from cd_monitor.services.notify_dispatcher import (
    NOTIFY_DECISIONS,
    _notifier_for,
    _resolve_channels,
    notify_opportunities,
)


def _opportunity(decision: str = "strong_alert", catalog: str = "SRCL-3520") -> Opportunity:
    item = MarketItem(
        source="wameiji",
        title=f"Test {catalog}",
        price=1000,
        currency="JPY",
    )
    return Opportunity(
        catalog_no=catalog,
        item=item,
        xianyu_reference_price=200,
        expected_sale_price=180,
        landed_cost=80,
        expected_revenue=180,
        expected_profit=100,
        net_margin=0.5,
        turnover_adjusted_roi=0.5,
        match_confidence=0.9,
        valid_xianyu_sample_count=5,
        liquidity_status="normal",
        decision=decision,
        risk_labels=[],
        opportunity_hash="abc",
    )


def _config(feishu: str = "", dingtalk: str = "") -> ProjectConfig:
    c = ProjectConfig()
    c.notify = NotifyConfig(feishu_webhook_url=feishu, dingtalk_webhook_url=dingtalk)
    return c


# === NOTIFY_DECISIONS =============================================
def test_notify_decisions_is_strong_and_weak_only():
    assert NOTIFY_DECISIONS == frozenset({"strong_alert", "weak_alert"})


# === _resolve_channels ============================================
def test_resolve_channels_all_with_both_configured():
    cfg = _config(feishu="https://fs", dingtalk="https://dt")
    assert _resolve_channels("all", cfg) == ["feishu", "dingtalk"]


def test_resolve_channels_all_with_only_feishu():
    cfg = _config(feishu="https://fs", dingtalk="")
    assert _resolve_channels("all", cfg) == ["feishu"]


def test_resolve_channels_all_with_nothing():
    cfg = _config()
    assert _resolve_channels("all", cfg) == []


def test_resolve_channels_specific_feishu():
    cfg = _config(feishu="https://fs")
    assert _resolve_channels("feishu", cfg) == ["feishu"]


def test_resolve_channels_specific_dingtalk():
    cfg = _config(dingtalk="https://dt")
    assert _resolve_channels("dingtalk", cfg) == ["dingtalk"]


def test_resolve_channels_empty():
    cfg = _config()
    assert _resolve_channels("", cfg) == []
    assert _resolve_channels("none", cfg) == []


def test_resolve_channels_unknown_spec_raises():
    cfg = _config()
    with pytest.raises(ValueError, match="Unknown notify channel spec"):
        _resolve_channels("slack", cfg)


# === _notifier_for =================================================
def test_notifier_for_feishu_dry_run_does_not_need_url():
    cfg = _config()
    n = _notifier_for("feishu", cfg, dry_run=True)
    assert n.channel == "feishu"


def test_notifier_for_dingtalk_dry_run_does_not_need_url():
    cfg = _config()
    n = _notifier_for("dingtalk", cfg, dry_run=True)
    assert n.channel == "dingtalk"


def test_notifier_for_feishu_real_uses_config_url():
    cfg = _config(feishu="https://fs.example/hook")
    n = _notifier_for("feishu", cfg, dry_run=False)
    assert n.channel == "feishu"
    assert n.webhook_url == "https://fs.example/hook"


def test_notifier_for_unknown_channel_raises():
    cfg = _config()
    with pytest.raises(ValueError, match="Unknown channel"):
        _notifier_for("slack", cfg, dry_run=True)


# === notify_opportunities =========================================
def test_notify_no_channels_returns_no_channel_configured(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DINGTALK_WEBHOOK_URL", raising=False)
    db = tmp_path / "db.sqlite"
    opps = [_opportunity()]
    results = notify_opportunities(
        db, [1], opps, channel_spec="feishu", config=_config(), dry_run=True
    )
    assert len(results) == 1
    assert results[0]["status"] == "no_channel_configured"
    assert results[0]["channel"] == "feishu"
    assert "notify.feishu_webhook_url" in results[0]["error"]


def test_notify_skips_review_only_by_default(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    opps = [_opportunity(decision="review_only")]
    results = notify_opportunities(
        db, [1], opps, channel_spec="feishu", config=_config(feishu="https://fs"), dry_run=True
    )
    assert len(results) == 1
    assert results[0]["status"] == "skipped"


def test_notify_skips_reject_even_when_include_review_only(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    opps = [_opportunity(decision="reject")]
    results = notify_opportunities(
        db, [1], opps,
        channel_spec="feishu",
        config=_config(feishu="https://fs"),
        dry_run=True,
        include_review_only=True,
    )
    assert results[0]["status"] == "skipped"


def test_notify_includes_review_only_when_flag_set(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    opps = [_opportunity(decision="review_only")]
    results = notify_opportunities(
        db, [1], opps,
        channel_spec="feishu",
        config=_config(feishu="https://fs"),
        dry_run=True,
        include_review_only=True,
    )
    assert results[0]["status"] == "dry_run"
    assert results[0]["channel"] == "feishu"


def test_notify_strong_alert_dry_run_records_alert(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    opps = [_opportunity(decision="strong_alert")]
    results = notify_opportunities(
        db, [1], opps, channel_spec="feishu", config=_config(feishu="https://fs"), dry_run=True
    )
    assert results[0]["status"] == "dry_run"
    assert results[0]["channel"] == "feishu"
    assert "message" in results[0]
    msg = results[0]["message"]
    assert msg["catalog_no"] == "SRCL-3520"
    assert "strong_alert" in msg["title"]


def test_notify_weak_alert_dry_run(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    opps = [_opportunity(decision="weak_alert")]
    results = notify_opportunities(
        db, [1], opps, channel_spec="feishu", config=_config(feishu="https://fs"), dry_run=True
    )
    assert results[0]["status"] == "dry_run"


def test_notify_all_channels_resolves_to_two_when_both_configured(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    opps = [_opportunity(decision="strong_alert")]
    results = notify_opportunities(
        db, [1], opps,
        channel_spec="all",
        config=_config(feishu="https://fs", dingtalk="https://dt"),
        dry_run=True,
    )
    assert len(results) == 2
    channels = [r["channel"] for r in results]
    assert channels == ["feishu", "dingtalk"]
    for r in results:
        assert r["status"] == "dry_run"
