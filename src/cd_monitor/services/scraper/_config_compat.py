"""兼容层：参考项目 src.config 的模块级常量。

把参考项目 src/config.py 中 scraper / ai_handler 用到的常量映射到
cd_monitor.infrastructure.config.settings，避免它们内部被大量重写。
"""
from cd_monitor.infrastructure.config.settings import (
    ai_settings,
    business_settings,
    fallback_ai_settings,
    notification_settings,
    scraper_settings,
    settings,
)


# ===== scraper 直接 import 的常量 =====
AI_DEBUG_MODE = ai_settings.debug_mode
LOGIN_IS_EDGE = scraper_settings.login_is_edge
RUN_HEADLESS = scraper_settings.run_headless
RUNNING_IN_DOCKER = scraper_settings.running_in_docker
SKIP_AI_ANALYSIS = ai_settings.skip_analysis
STATE_FILE = scraper_settings.state_file

# 业务常量（scraper 不直接 import 但 parse / handler 会用到）
DETAIL_API_URL_PATTERN = "h5api.m.goofish.com/h5/mtop.taobao.idle.pc.detail"
SEARCH_API_URL_PATTERN = "h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.search"


# ===== ai_handler 直接 import 的常量 =====
IMAGE_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
IMAGE_SAVE_DIR = settings.image_save_dir
TASK_IMAGE_DIR_PREFIX = settings.task_image_dir_prefix
MODEL_NAME = ai_settings.model_name
ENABLE_RESPONSE_FORMAT = ai_settings.enable_response_format

# 单例 OpenAI 客户端：与参考项目 src.config.client 等价。
# 如果没配置 api_key，client 为 None；调用方需做 None 检查。
def _build_default_client():
    if not ai_settings.is_configured():
        return None
    try:
        from openai import AsyncOpenAI
        return AsyncOpenAI(api_key=ai_settings.api_key, base_url=ai_settings.base_url)
    except Exception:
        return None

client = _build_default_client()


__all__ = [
    # scraper
    "AI_DEBUG_MODE",
    "DETAIL_API_URL_PATTERN",
    "LOGIN_IS_EDGE",
    "RUN_HEADLESS",
    "RUNNING_IN_DOCKER",
    "SKIP_AI_ANALYSIS",
    "STATE_FILE",
    "SEARCH_API_URL_PATTERN",
    # ai_handler
    "IMAGE_DOWNLOAD_HEADERS",
    "IMAGE_SAVE_DIR",
    "TASK_IMAGE_DIR_PREFIX",
    "MODEL_NAME",
    "ENABLE_RESPONSE_FORMAT",
    "client",
]
