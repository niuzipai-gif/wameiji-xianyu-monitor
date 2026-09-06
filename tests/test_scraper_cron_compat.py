"""Smoke tests for the scraper package's cron compatibility bridge."""

from apscheduler.triggers.cron import CronTrigger

from cd_monitor.services.scraper._cron_utils_compat import (
    build_cron_trigger,
    validate_cron_expression,
)


def test_scraper_compat_reuses_core_cron_contract() -> None:
    assert validate_cron_expression("@daily") == "0 0 * * *"
    trigger = build_cron_trigger("0 8 * * *")
    assert isinstance(trigger, CronTrigger)
