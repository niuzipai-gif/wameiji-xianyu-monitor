"""Compatibility bridge for the migrated scraper service package.

The scraper task and scheduler modules were moved under
``cd_monitor.services.scraper`` while cron parsing remains a shared core
utility.  Keeping this small module preserves the import path used by the
migrated modules without duplicating cron semantics.
"""
from __future__ import annotations

from cd_monitor.core.cron_utils import build_cron_trigger, validate_cron_expression

__all__ = ["build_cron_trigger", "validate_cron_expression"]
