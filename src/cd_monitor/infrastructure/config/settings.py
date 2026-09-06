"""统一配置管理模块
使用 Pydantic 进行类型安全的配置管理。

主 AI 默认走 MiniMax M3（多模态），文本降级可走 DeepSeek 备用。
"""
try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    _USING_PYDANTIC_SETTINGS = True
except ImportError:
    from pydantic import BaseSettings
    _USING_PYDANTIC_SETTINGS = False
from pydantic import Field
from typing import Optional
import os


def _env_field(default, env_name: str, **kwargs):
    if _USING_PYDANTIC_SETTINGS:
        return Field(default, validation_alias=env_name, **kwargs)
    return Field(default, env=env_name, **kwargs)


if _USING_PYDANTIC_SETTINGS:
    class _EnvSettings(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            extra="ignore",
            protected_namespaces=(),
        )
else:
    class _EnvSettings(BaseSettings):
        class Config:
            env_file = ".env"
            env_file_encoding = "utf-8"
            extra = "ignore"
            protected_namespaces = ()


class AISettings(_EnvSettings):
    """主 AI 模型配置（默认 MiniMax M3 多模态）"""
    api_key: Optional[str] = _env_field(None, "OPENAI_API_KEY")
    base_url: str = _env_field("https://api.minimaxi.com/v1", "OPENAI_BASE_URL")
    model_name: str = _env_field("MiniMax-Text-01", "OPENAI_MODEL_NAME")
    proxy_url: Optional[str] = _env_field(None, "PROXY_URL")
    debug_mode: bool = _env_field(False, "AI_DEBUG_MODE")
    enable_response_format: bool = _env_field(True, "ENABLE_RESPONSE_FORMAT")
    enable_thinking: bool = _env_field(False, "ENABLE_THINKING")
    skip_analysis: bool = _env_field(False, "SKIP_AI_ANALYSIS")

    def is_configured(self) -> bool:
        return bool(self.base_url and self.model_name and self.api_key)


class FallbackAISettings(_EnvSettings):
    """备用 AI 配置（DeepSeek，纯文本，无图片分析能力）"""
    enabled: bool = _env_field(False, "FALLBACK_AI_ENABLED")
    api_key: Optional[str] = _env_field(None, "DEEPSEEK_API_KEY")
    base_url: str = _env_field("https://api.deepseek.com/v1", "DEEPSEEK_BASE_URL")
    model_name: str = _env_field("deepseek-chat", "DEEPSEEK_MODEL_NAME")
    proxy_url: Optional[str] = _env_field(None, "DEEPSEEK_PROXY_URL")

    def is_configured(self) -> bool:
        return bool(self.enabled and self.api_key and self.base_url and self.model_name)


class NotificationSettings(_EnvSettings):
    """通知服务配置"""
    ntfy_topic_url: Optional[str] = _env_field(None, "NTFY_TOPIC_URL")
    gotify_url: Optional[str] = _env_field(None, "GOTIFY_URL")
    gotify_token: Optional[str] = _env_field(None, "GOTIFY_TOKEN")
    bark_url: Optional[str] = _env_field(None, "BARK_URL")
    wx_bot_url: Optional[str] = _env_field(None, "WX_BOT_URL")
    telegram_bot_token: Optional[str] = _env_field(None, "TELEGRAM_BOT_TOKEN")
    telegram_chat_id: Optional[str] = _env_field(None, "TELEGRAM_CHAT_ID")
    telegram_api_base_url: Optional[str] = _env_field(
        "https://api.telegram.org", "TELEGRAM_API_BASE_URL",
    )

    DEFAULT_TELEGRAM_API_BASE_URL: str = "https://api.telegram.org"

    feishu_webhook_url: Optional[str] = _env_field(None, "FEISHU_WEBHOOK_URL")
    dingtalk_webhook_url: Optional[str] = _env_field(None, "DINGTALK_WEBHOOK_URL")
    feishu_webhook_secret: Optional[str] = _env_field(None, "FEISHU_WEBHOOK_SECRET")
    dingtalk_webhook_secret: Optional[str] = _env_field(None, "DINGTALK_WEBHOOK_SECRET")
    webhook_url: Optional[str] = _env_field(None, "WEBHOOK_URL")
    webhook_method: str = _env_field("POST", "WEBHOOK_METHOD")
    webhook_headers: Optional[str] = _env_field(None, "WEBHOOK_HEADERS")
    webhook_content_type: str = _env_field("JSON", "WEBHOOK_CONTENT_TYPE")
    webhook_query_parameters: Optional[str] = _env_field(None, "WEBHOOK_QUERY_PARAMETERS")
    webhook_body: Optional[str] = _env_field(None, "WEBHOOK_BODY")
    pcurl_to_mobile: bool = _env_field(True, "PCURL_TO_MOBILE")

    def has_any_notification_enabled(self) -> bool:
        return any([
            self.ntfy_topic_url,
            self.wx_bot_url,
            self.gotify_url and self.gotify_token,
            self.bark_url,
            self.telegram_bot_token and self.telegram_chat_id,
            self.webhook_url,
            self.feishu_webhook_url,
            self.dingtalk_webhook_url,
        ])


class ScraperSettings(_EnvSettings):
    """爬虫相关配置"""
    run_headless: bool = _env_field(True, "RUN_HEADLESS")
    login_is_edge: bool = _env_field(False, "LOGIN_IS_EDGE")
    running_in_docker: bool = _env_field(False, "RUNNING_IN_DOCKER")
    state_file: str = _env_field("data/xianyu_state.json", "STATE_FILE")
    account_state_dir: str = _env_field("data/state", "ACCOUNT_STATE_DIR")

    account_rotation_enabled: bool = _env_field(False, "ACCOUNT_ROTATION_ENABLED")
    account_rotation_mode: str = _env_field("per_task", "ACCOUNT_ROTATION_MODE")
    account_retry_limit: int = _env_field(2, "ACCOUNT_ROTATION_RETRY_LIMIT")
    account_blacklist_ttl: int = _env_field(300, "ACCOUNT_BLACKLIST_TTL")

    proxy_rotation_enabled: bool = _env_field(False, "PROXY_ROTATION_ENABLED")
    proxy_pool: str = _env_field("", "PROXY_POOL")
    proxy_retry_limit: int = _env_field(2, "PROXY_ROTATION_RETRY_LIMIT")
    proxy_blacklist_ttl: int = _env_field(300, "PROXY_BLACKLIST_TTL")

    task_failure_threshold: int = _env_field(3, "TASK_FAILURE_THRESHOLD")
    task_failure_pause_seconds: int = _env_field(600, "TASK_FAILURE_PAUSE_SECONDS")

    image_download_concurrency: int = _env_field(3, "IMAGE_DOWNLOAD_CONCURRENCY")
    ai_analysis_concurrency: int = _env_field(2, "AI_ANALYSIS_CONCURRENCY")
    seller_profile_cache_ttl: int = _env_field(1800, "SELLER_PROFILE_CACHE_TTL")


class BusinessSettings(_EnvSettings):
    """业务层配置（与原 cd_monitor.config / config.example.yaml 兼容）"""
    db_path: str = _env_field("data/cd_monitor.db", "CD_DB_PATH")
    config_path: str = _env_field("config.yaml", "CD_CONFIG_PATH")
    snapshot_dir: str = _env_field("data/snapshots", "CD_SNAPSHOT_DIR")
    screenshot_dir: str = _env_field("data/screenshots", "CD_SCREENSHOT_DIR")
    image_dir: str = _env_field("data/images", "CD_IMAGE_DIR")

    profit_margin_min: float = _env_field(0.15, "CD_PROFIT_MARGIN_MIN")
    roi_min: float = _env_field(0.20, "CD_ROI_MIN")
    xianyu_sample_limit: int = _env_field(20, "CD_XIANYU_SAMPLE_LIMIT")
    xianyu_min_valid_price_cny: float = _env_field(5.0, "CD_XIANYU_MIN_PRICE")
    xianyu_max_valid_price_cny: float = _env_field(20000.0, "CD_XIANYU_MAX_PRICE")
    match_confidence_threshold: float = _env_field(0.55, "CD_MATCH_CONFIDENCE")
    weak_alert_threshold: float = _env_field(0.08, "CD_WEAK_ALERT_THRESHOLD")
    strong_alert_threshold: float = _env_field(0.20, "CD_STRONG_ALERT_THRESHOLD")

    wameiji_jpy_to_cny: float = _env_field(0.05, "CD_JPY_TO_CNY")
    wameiji_international_shipping_cny: float = _env_field(80.0, "CD_INTL_SHIPPING")
    wameiji_customs_duty_rate: float = _env_field(0.10, "CD_CUSTOMS_RATE")

    def sqlite_uri(self) -> str:
        return self.db_path


class AppSettings(_EnvSettings):
    """应用主配置"""
    server_port: int = _env_field(8000, "SERVER_PORT")
    web_username: str = _env_field("admin", "WEB_USERNAME")
    web_password: str = _env_field("admin123", "WEB_PASSWORD")
    task_log_retention_days: int = _env_field(7, "TASK_LOG_RETENTION_DAYS", ge=1)

    config_file: str = "config.json"
    image_save_dir: str = "images"
    task_image_dir_prefix: str = "task_images_"
    state_dir: str = "state"
    log_dir: str = "logs"
    prompts_dir: str = "prompts"
    jsonl_dir: str = "jsonl"
    price_history_dir: str = "price_history"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        for d in (self.image_save_dir, self.state_dir, self.log_dir,
                  self.prompts_dir, self.jsonl_dir, self.price_history_dir):
            os.makedirs(d, exist_ok=True)


_settings_instance = None

def get_settings() -> AppSettings:
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = AppSettings()
    return _settings_instance


def reload_settings() -> None:
    global _settings_instance, settings, ai_settings, fallback_ai_settings
    global notification_settings, scraper_settings, business_settings
    from dotenv import load_dotenv
    from cd_monitor.infrastructure.config.env_manager import env_manager

    load_dotenv(dotenv_path=env_manager.env_file, override=True)
    _settings_instance = None
    settings = get_settings()
    ai_settings = AISettings()
    fallback_ai_settings = FallbackAISettings()
    notification_settings = NotificationSettings()
    scraper_settings = ScraperSettings()
    business_settings = BusinessSettings()


# Module-level 常量：参考项目的 src.config.DEFAULT_TELEGRAM_API_BASE_URL
DEFAULT_TELEGRAM_API_BASE_URL = "https://api.telegram.org"

settings = get_settings()
ai_settings = AISettings()
fallback_ai_settings = FallbackAISettings()
notification_settings = NotificationSettings()
scraper_settings = ScraperSettings()
business_settings = BusinessSettings()
