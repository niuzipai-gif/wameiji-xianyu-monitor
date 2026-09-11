from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

from cd_monitor.services.reference_memory import (
    ExtractedReferenceSample,
    import_reference_samples,
    record_reference_market_observation,
)
from cd_monitor.web_server import create_server


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
