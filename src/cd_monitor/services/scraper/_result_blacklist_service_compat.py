"""兼容层：re-export _result_blacklist_service 的符号。"""
from cd_monitor.services.scraper._result_blacklist_service import (
    match_blacklist_keywords,
    normalize_blacklist_keywords,
)

__all__ = ["match_blacklist_keywords", "normalize_blacklist_keywords"]
