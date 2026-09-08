from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_docker_runtime_has_one_database_and_no_browser_session_surface() -> None:
    compose = (PROJECT_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "CD_DB_PATH: /app/data/cd_monitor.db" in compose
    assert "CD_DB: /app/data/cd_monitor.db" in compose
    assert 'BROWSER_ENABLED: "false"' in compose
    assert 'DUAL_MARKET_COLLECTION_PAUSED: "true"' in compose
    assert "- ./data:/app/data" in compose

    for forbidden in (
        "wameiji_db",
        "/var/lib/cd_monitor",
        "STATE_FILE:",
        "ACCOUNT_STATE_DIR:",
        "./state:/app/state",
    ):
        assert forbidden not in compose

    assert "CD_DB_PATH=/app/data/cd_monitor.db" in dockerfile
    assert "playwright" not in dockerfile.casefold()
    assert "/app/state" not in dockerfile
