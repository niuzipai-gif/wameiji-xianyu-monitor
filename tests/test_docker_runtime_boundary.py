from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_docker_runtime_uses_a_dedicated_non_secret_host_directory() -> None:
    compose = (PROJECT_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "CD_DB_PATH: /app/data/cd_monitor.db" in compose
    assert "CD_DB: /app/data/cd_monitor.db" in compose
    assert "CD_MONITOR_DB_PATH: /app/data/cd_monitor.db" in compose
    assert 'BROWSER_ENABLED: "false"' in compose
    assert 'DUAL_MARKET_COLLECTION_PAUSED: "true"' in compose
    assert "- ./docker-runtime-data:/app/data" in compose
    assert "- ./data:/app/data" not in compose

    database_paths = set(re.findall(r"/app/data/[A-Za-z0-9_.-]+\.db", compose))
    assert database_paths == {"/app/data/cd_monitor.db"}

    for evidence_path in (
        "/app/data/imported-evidence/snapshots",
        "/app/data/imported-evidence/screenshots",
        "/app/data/imported-evidence/images",
    ):
        assert evidence_path in compose
        assert evidence_path in dockerfile

    for forbidden in (
        "wameiji_db",
        "/var/lib/cd_monitor",
        "STATE_FILE:",
        "ACCOUNT_STATE_DIR:",
        "./state:/app/state",
        "./data:/app/data",
        "browser_profiles",
        "xianyu_state",
        "wameiji_state",
    ):
        assert forbidden not in compose

    assert "CD_DB_PATH=/app/data/cd_monitor.db" in dockerfile
    assert "playwright" not in dockerfile.casefold()
    assert "/app/state" not in dockerfile
