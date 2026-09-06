"""兼容层：把 utils.py 的符号按 src.utils 命名风格暴露。
scraper 内部统一从 _utils_compat 导入，未来替换实现不影响调用方。
"""
from cd_monitor.services.scraper.utils import (
    retry_on_failure,
    log_time,
    sanitize_filename,
    build_task_log_path,
    resolve_task_log_path,
    convert_goofish_link,
    get_link_unique_key,
    format_registration_days,
    safe_get,
    random_sleep,
    save_to_jsonl,
)

__all__ = [
    "retry_on_failure",
    "log_time",
    "sanitize_filename",
    "build_task_log_path",
    "resolve_task_log_path",
    "convert_goofish_link",
    "get_link_unique_key",
    "format_registration_days",
    "safe_get",
    "random_sleep",
    "save_to_jsonl",
]
