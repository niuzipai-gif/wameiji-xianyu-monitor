from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE = "data/local/dual-market.db"


def script(name: str) -> str:
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_all_collector_launchers_default_to_the_active_selection_database() -> None:
    for name in ("start-local.ps1", "start-replica.ps1", "start-discovery.ps1"):
        assert f'[string]$Database = "{DATABASE}"' in script(name)


def test_primary_and_autostart_launchers_pass_one_database_to_every_service() -> None:
    start_all = script("start-all.ps1")
    assert f'[string]$Database = "{DATABASE}"' in start_all
    assert start_all.count('"-Database", $Database') == 3
    autostart = script("install-collector-autostart.ps1")
    assert autostart.count(f'"-Database", "{DATABASE}"') == 3
