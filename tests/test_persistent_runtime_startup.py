from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PERSISTENT_DATABASE = "data/local/dual-market.db"


def test_service_launchers_share_the_persistent_dual_market_database() -> None:
    launchers = (
        PROJECT_ROOT / "scripts" / "start-local.ps1",
        PROJECT_ROOT / "scripts" / "start-replica.ps1",
        PROJECT_ROOT / "scripts" / "start-discovery.ps1",
    )

    for launcher in launchers:
        source = launcher.read_text(encoding="utf-8")
        assert PERSISTENT_DATABASE in source, launcher
        assert "data/local/takeover.db" not in source, launcher

    publisher = (PROJECT_ROOT / "scripts" / "publish-replica.py").read_text(
        encoding="utf-8"
    )
    assert PERSISTENT_DATABASE in publisher
    assert "data/local/takeover.db" not in publisher


def test_autostart_and_manual_launcher_pass_one_database_to_every_service() -> None:
    installer = (
        PROJECT_ROOT / "scripts" / "install-collector-autostart.ps1"
    ).read_text(encoding="utf-8")
    manual = (PROJECT_ROOT / "scripts" / "start-all.ps1").read_text(
        encoding="utf-8"
    )

    assert '[string]$Database = "data/local/dual-market.db"' in installer
    assert installer.count('"-Database", $Database') == 3
    assert '[string]$Database = "data/local/dual-market.db"' in manual
    assert manual.count('"-Database", $Database') == 3


def test_render_command_queue_is_explicitly_enabled_for_the_local_collector() -> None:
    deployment = (PROJECT_ROOT / "render.yaml").read_text(encoding="utf-8")

    assert "DUAL_MARKET_COLLECTION_PAUSED" in deployment
    assert 'value: "false"' in deployment
