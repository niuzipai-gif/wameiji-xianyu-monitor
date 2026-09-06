from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cd_monitor.core.models import CostConfig, EvaluationConfig


@dataclass(slots=True)
class AppConfig:
    db_path: str = "data/cd_monitor.db"
    snapshot_dir: str = "data/snapshots"
    screenshot_dir: str = "data/screenshots"


@dataclass(slots=True)
class BrowserConfig:
    enabled: bool = False
    profile_name: str = "default"
    xianyu_state_file: str = ""
    xianyu_profile_dir: str = ""          # 闲鱼真实 Playwright user-data-dir；空=不读
    min_delay_seconds: int = 5
    max_delay_seconds: int = 12
    max_retries_per_action: int = 2
    stop_on_captcha: bool = True
    stop_on_security_check: bool = True

    # === Wameiji 真实浏览器读取配置（spec §3.1） ===
    wameiji_state_file: str = ""             # 来自 Wameiji Chrome 扩展导出的 storage_state JSON
    wameiji_profile_dir: str = ""           # 真实 Chrome user-data-dir；空=不读
    wameiji_search_url: str = "https://meruki.cn/search"
    wameiji_headless: bool = False          # 默认有头，便于首次扫码/登录
    wameiji_max_consecutive_failures: int = 3   # spec §1.2 Browser-Harness
    wameiji_max_retries_per_action: int = 2    # spec §1.2 Browser-Harness
    wameiji_page_load_timeout_ms: int = 30_000
    wameiji_result_timeout_ms: int = 20_000


@dataclass(slots=True)
class NotifyConfig:
    feishu_webhook_url: str = ""
    dingtalk_webhook_url: str = ""


@dataclass(slots=True)
class ProjectConfig:
    app: AppConfig = field(default_factory=AppConfig)
    cost: CostConfig = field(default_factory=CostConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)


def load_config(path: str | Path | None = None) -> ProjectConfig:
    data = _parse_simple_yaml(Path(path)) if path and Path(path).exists() else {}
    config = ProjectConfig(
        app=AppConfig(**_section(data, "app")),
        cost=CostConfig(**_section(data, "cost")),
        evaluation=_evaluation_config(data),
        browser=BrowserConfig(**_section(data, "browser")),
        notify=NotifyConfig(**_section(data, "notify")),
    )
    _apply_env(config)
    return config


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    return value if isinstance(value, dict) else {}


def _evaluation_config(data: dict[str, Any]) -> EvaluationConfig:
    xianyu = _section(data, "xianyu")
    alert = _section(data, "alert")
    payload: dict[str, Any] = {}
    mapping = {
        "min_valid_price_cny": "min_valid_price_cny",
        "max_valid_price_cny": "max_valid_price_cny",
        "sample_limit": "sample_limit",
        "negotiation_discount": "negotiation_discount",
        "liquidity_discount_default": "liquidity_discount_default",
        "fee_rate_default": "xianyu_fee_rate",
        "fee_cap_cny": "xianyu_fee_cap_cny",
        "strong_profit_min_cny": "strong_profit_min_cny",
        "strong_margin_min": "strong_margin_min",
        "weak_profit_min_cny": "weak_profit_min_cny",
        "weak_margin_min": "weak_margin_min",
        "min_match_confidence_strong": "min_match_confidence_strong",
        "min_match_confidence_weak": "min_match_confidence_weak",
        "min_xianyu_samples_strong": "min_xianyu_samples_strong",
        "min_xianyu_samples_weak": "min_xianyu_samples_weak",
    }
    for source in (xianyu, alert):
        for key, target in mapping.items():
            if key in source:
                payload[target] = source[key]
    return EvaluationConfig(**payload)


def _apply_env(config: ProjectConfig) -> None:
    if os.getenv("CD_MONITOR_DB_PATH"):
        config.app.db_path = os.environ["CD_MONITOR_DB_PATH"]
    if os.getenv("FEISHU_WEBHOOK_URL"):
        config.notify.feishu_webhook_url = os.environ["FEISHU_WEBHOOK_URL"]
    if os.getenv("DINGTALK_WEBHOOK_URL"):
        config.notify.dingtalk_webhook_url = os.environ["DINGTALK_WEBHOOK_URL"]
    if os.getenv("BROWSER_ENABLED"):
        config.browser.enabled = _to_bool(os.environ["BROWSER_ENABLED"])
    if os.getenv("XIANYU_STATE_FILE"):
        config.browser.xianyu_state_file = os.environ["XIANYU_STATE_FILE"]
    elif os.getenv("GOOFISH_STATE_FILE"):
        config.browser.xianyu_state_file = os.environ["GOOFISH_STATE_FILE"]
    if os.getenv("XIANYU_PROFILE_DIR"):
        config.browser.xianyu_profile_dir = os.environ["XIANYU_PROFILE_DIR"]
    elif os.getenv("GOOFISH_PROFILE_DIR"):
        config.browser.xianyu_profile_dir = os.environ["GOOFISH_PROFILE_DIR"]
    # === Wameiji 真实浏览器环境变量（spec §3.1） ===
    if os.getenv("WAMEIJI_STATE_FILE"):
        config.browser.wameiji_state_file = os.environ["WAMEIJI_STATE_FILE"]
    if os.getenv("WAMEIJI_PROFILE_DIR"):
        config.browser.wameiji_profile_dir = os.environ["WAMEIJI_PROFILE_DIR"]
    if os.getenv("WAMEIJI_SEARCH_URL"):
        config.browser.wameiji_search_url = os.environ["WAMEIJI_SEARCH_URL"]
    if os.getenv("WAMEIJI_HEADLESS"):
        config.browser.wameiji_headless = _to_bool(os.environ["WAMEIJI_HEADLESS"])


def _parse_simple_yaml(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current: dict[str, Any] | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and line.endswith(":"):
            key = line[:-1].strip()
            current = {}
            result[key] = current
            continue
        if current is not None and line.startswith("  ") and ":" in line:
            key, value = line.strip().split(":", 1)
            current[key.strip()] = _parse_scalar(value.strip())
    return result


def _parse_scalar(value: str) -> Any:
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"", "null", "none"}:
        return ""
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
