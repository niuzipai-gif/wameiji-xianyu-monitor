from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

from cd_monitor import web_server
from cd_monitor.core.discovery import DiscoveryCandidate
from cd_monitor.services.reference_memory import (
    ExtractedReferenceSample,
    import_reference_samples,
    record_reference_market_observation,
)
from cd_monitor.web_server import create_server
from cd_monitor.storage.sqlite import init_db, list_discovery_pools, upsert_discovery_candidate


def _get_json(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json_with_status(url: str) -> tuple[int, dict[str, object]]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


class _FixtureExtractor:
    def extract(self, _path: Path) -> ExtractedReferenceSample:
        return ExtractedReferenceSample(
            checksum_sha256="e" * 64,
            barcode="4547366558180",
            extracted_text="milet Walkin In My Lane",
            extraction_state="fixture",
        )


def _seed_reference_observation(
    tmp_path: Path, db_path: Path
) -> tuple[dict[str, object], int]:
    folder = tmp_path / "samples"
    folder.mkdir()
    (folder / "milet.png").write_bytes(b"fixture")
    import_reference_samples(db_path, folder, extractor=_FixtureExtractor())
    with sqlite3.connect(db_path) as conn:
        product_id = int(conn.execute("SELECT id FROM reference_products").fetchone()[0])
    observation = record_reference_market_observation(
        db_path,
        product_id=product_id,
        market="wameiji",
        observation_state="found",
        observed_title="milet Walkin In My Lane 初回限定盤",
        version_evidence="初回限定盤",
        barcode="4547366558180",
        price=2895.0,
        currency="JPY",
        source_url="https://meruki.example/item/milet",
    )
    return observation, product_id


def _seed_direction_candidate(db_path: Path) -> int:
    init_db(db_path)
    pool = list_discovery_pools(db_path)[0]
    return upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id or 1,
            media_type="fixture",
            identity_key="fixture:direction-candidate",
            title="初回限定 CD",
            raw_text="店铺规则",
        ),
    )


def test_reference_import_cli_requires_explicit_folder_and_reports_local_counts(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reference.db"
    folder = tmp_path / "empty-samples"
    folder.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cd_monitor.cli",
            "reference-import",
            "--db",
            str(db_path),
            "--folder",
            str(folder),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(result.stdout)
    assert payload["input_images"] == 0
    assert payload["imported_samples"] == 0
    assert payload["existing_samples"] == 0
    assert payload["product_count"] == 0
    assert payload["barcode_sample_count"] == 0
    assert isinstance(payload["ocr_languages"], list)
    assert payload["network_requests"] == 0


def test_reference_memory_api_only_exposes_read_only_status_and_matches(tmp_path: Path) -> None:
    db_path = tmp_path / "reference.db"
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status = _get_json(f"{base_url}/api/reference-memory/status")
        matches = _get_json(f"{base_url}/api/reference-memory/matches?limit=20")

        assert status["product_count"] == 0
        assert status["sample_count"] == 0
        assert status["network_requests"] == 0
        assert matches == {"items": []}
    finally:
        server.shutdown()
        server.server_close()


def test_reference_direction_endpoints_are_read_only(tmp_path: Path) -> None:
    db_path = tmp_path / "reference.db"
    _observation, _product_id = _seed_reference_observation(tmp_path, db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE reference_product_samples SET extracted_text = '初回限定 CD'"
        )
    candidate_id = _seed_direction_candidate(db_path)
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        directions = _get_json(f"{base_url}/api/reference-memory/directions")
        candidates = _get_json(
            f"{base_url}/api/reference-memory/candidate-directions?limit=20"
        )

        assert directions == {
            "source": "approved_reference_samples",
            "reference_product_count": 1,
            "network_requests": 0,
            "directions": [
                {
                    "key": "physical_music",
                    "label": "实体音乐",
                    "reference_product_count": 1,
                    "reference_share": 1.0,
                },
                {
                    "key": "limited_or_first_edition",
                    "label": "初回/限定/特典",
                    "reference_product_count": 1,
                    "reference_share": 1.0,
                },
            ],
        }
        assert candidates == {
            "items": [
                {
                    "candidate_id": candidate_id,
                    "directions": directions["directions"],
                    "state": "positive_direction_covered",
                    "decision_effect": "none",
                }
            ]
        }
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("raw_limit", ["0", "201", "abc"])
def test_reference_candidate_directions_api_rejects_invalid_limit(
    tmp_path: Path, raw_limit: str
) -> None:
    db_path = tmp_path / "reference.db"
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, payload = _get_json_with_status(
            f"{base_url}/api/reference-memory/candidate-directions?limit={raw_limit}"
        )

        assert status == 400
        assert payload == {"error": "invalid_limit"}
    finally:
        server.shutdown()
        server.server_close()


def test_reference_directions_api_masks_local_read_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "reference.db"

    def fail_direction_read(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise sqlite3.OperationalError("fixture read failure")

    monkeypatch.setattr(web_server, "list_reference_direction_summary", fail_direction_read)
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, payload = _get_json_with_status(
            f"{base_url}/api/reference-memory/directions"
        )

        assert status == 500
        assert payload == {"error": "reference_directions_unavailable"}
    finally:
        server.shutdown()
        server.server_close()


def test_reference_memory_observations_api_reads_recorded_market_evidence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reference.db"
    observation, _product_id = _seed_reference_observation(tmp_path, db_path)
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        code, payload = _get_json_with_status(
            f"{base_url}/api/reference-memory/observations?limit=20"
        )

        assert code == 200
        assert payload == {"items": [observation]}
    finally:
        server.shutdown()
        server.server_close()


def test_reference_memory_profiles_api_exposes_evidence_and_latest_market_state(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reference.db"
    observation, product_id = _seed_reference_observation(tmp_path, db_path)
    record_reference_market_observation(
        db_path,
        product_id=product_id,
        market="xianyu",
        observation_state="found",
        observed_title="milet Walkin In My Lane 初回限定盤",
        catalog_no="SECL-9999",
        observed_at="2026-09-11T00:00:00+00:00",
    )
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status = _get_json(f"{base_url}/api/reference-memory/status")
        profiles = _get_json(f"{base_url}/api/reference-memory/profiles?limit=1")
        error_status, error = _get_json_with_status(
            f"{base_url}/api/reference-memory/profiles?limit=0"
        )

        assert status["latest_market_coverage"]["xianyu"]["states"] == {"found": 1}
        assert profiles["items"][0]["product_id"] == product_id
        assert profiles["items"][0]["markets"]["wameiji"] == observation
        assert profiles["items"][0]["markets"]["xianyu"]["catalog_no"] == "SECL-9999"
        assert error_status == 400
        assert error == {"error": "invalid_limit"}
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    "raw_limit",
    ["", "abc", "1.5", "+1", "-1", "1_0", "0", "201", "9" * 5000],
)
def test_reference_memory_profiles_api_rejects_invalid_limit_spellings(
    tmp_path: Path, raw_limit: str
) -> None:
    db_path = tmp_path / "reference.db"
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, payload = _get_json_with_status(
            f"{base_url}/api/reference-memory/profiles?limit={raw_limit}"
        )

        assert status == 400
        assert payload == {"error": "invalid_limit"}
    finally:
        server.shutdown()
        server.server_close()


def test_reference_memory_profiles_api_masks_malformed_persisted_evidence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reference.db"
    _observation, product_id = _seed_reference_observation(tmp_path, db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE reference_products SET tokens_json = ? WHERE id = ?",
            ("not-json", product_id),
        )
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, payload = _get_json_with_status(
            f"{base_url}/api/reference-memory/profiles"
        )

        assert status == 500
        assert payload == {"error": "reference_profiles_unavailable"}
    finally:
        server.shutdown()
        server.server_close()


def test_reference_memory_profiles_api_masks_sqlite_read_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "reference.db"

    def fail_profile_read(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        raise sqlite3.OperationalError("fixture read failure")

    monkeypatch.setattr(web_server, "list_reference_product_profiles", fail_profile_read)
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        status, payload = _get_json_with_status(
            f"{base_url}/api/reference-memory/profiles?limit=1"
        )

        assert status == 500
        assert payload == {"error": "reference_profiles_unavailable"}
    finally:
        server.shutdown()
        server.server_close()
