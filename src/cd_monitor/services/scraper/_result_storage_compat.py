"""兼容层：re-export result_storage_service。"""
from cd_monitor.services.scraper._result_storage_service import (
    load_processed_link_keys,
    save_result_record,
)

__all__ = ["load_processed_link_keys", "save_result_record"]
