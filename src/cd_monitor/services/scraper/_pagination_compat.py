"""兼容层：re-export search_pagination。"""
from cd_monitor.services.scraper._search_pagination import (
    advance_search_page,
    is_search_results_response,
)

__all__ = ["advance_search_page", "is_search_results_response"]
