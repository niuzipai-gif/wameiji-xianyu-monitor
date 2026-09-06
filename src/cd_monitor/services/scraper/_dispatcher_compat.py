"""兼容层：re-export item_analysis_dispatcher。"""
from cd_monitor.services.scraper._item_analysis_dispatcher import (
    ItemAnalysisDispatcher,
    ItemAnalysisJob,
)

__all__ = ["ItemAnalysisDispatcher", "ItemAnalysisJob"]
