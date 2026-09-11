from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from cd_monitor.core.discovery import DiscoveryCandidate
from cd_monitor.services.reference_memory import (
    ExtractedReferenceSample,
    _parse_tesseract_languages,
    import_reference_samples,
    read_candidate_reference_match,
    record_reference_market_observation,
    refresh_discovery_candidate_reference_match,
)
from cd_monitor.storage.sqlite import list_discovery_pools, upsert_discovery_candidate


@dataclass
class FakeExtractor:
    samples: dict[str, ExtractedReferenceSample]

    def extract(self, path: Path) -> ExtractedReferenceSample:
        return self.samples[path.name]


@dataclass
class CountingChecksumExtractor:
    calls: int = 0

    def extract(self, path: Path) -> ExtractedReferenceSample:
        self.calls += 1
        return ExtractedReferenceSample(
            checksum_sha256=sha256(path.read_bytes()).hexdigest(),
            barcode=None,
            extracted_text="unique product title",
            extraction_state="fixture",
        )


def _write_sample_files(folder: Path, *names: str) -> None:
    folder.mkdir()
    for name in names:
        (folder / name).write_bytes(b"fixture image bytes")


def test_import_reference_samples_is_idempotent_and_groups_exact_barcodes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reference.db"
    folder = tmp_path / "samples"
    _write_sample_files(folder, "one.png", "two.png")
    extractor = FakeExtractor(
        {
            "one.png": ExtractedReferenceSample(
                checksum_sha256="a" * 64,
                barcode="4547366558180",
                extracted_text="milet Walkin In My Lane",
                extraction_state="fixture",
            ),
            "two.png": ExtractedReferenceSample(
                checksum_sha256="b" * 64,
                barcode="4547366558180",
                extracted_text="milet Walkin In My Lane CD",
                extraction_state="fixture",
            ),
        }
    )

    first = import_reference_samples(db_path, folder, extractor=extractor)
    second = import_reference_samples(db_path, folder, extractor=extractor)

    assert first.input_images == 2
    assert first.imported_samples == 2
    assert first.existing_samples == 0
    assert first.product_count == 1
    assert first.barcode_sample_count == 2
    assert first.network_requests == 0
    assert second.imported_samples == 0
    assert second.existing_samples == 2
    assert second.product_count == 1

    with sqlite3.connect(db_path) as conn:
        products = conn.execute(
            "SELECT stable_key, barcode, sample_count FROM reference_products"
        ).fetchall()
        samples = conn.execute(
            "SELECT checksum_sha256, product_id FROM reference_product_samples ORDER BY id"
        ).fetchall()
    assert products == [("jan:4547366558180", "4547366558180", 2)]
    assert [row[0] for row in samples] == ["a" * 64, "b" * 64]
    assert len({row[1] for row in samples}) == 1


def test_idempotent_reimport_skips_costly_extraction_when_file_hash_already_exists(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reference.db"
    folder = tmp_path / "samples"
    _write_sample_files(folder, "one.png")
    extractor = CountingChecksumExtractor()

    import_reference_samples(db_path, folder, extractor=extractor)
    report = import_reference_samples(db_path, folder, extractor=extractor)

    assert extractor.calls == 1
    assert report.imported_samples == 0
    assert report.existing_samples == 1


def test_unbarcoded_samples_remain_separate_even_when_text_is_identical(tmp_path: Path) -> None:
    db_path = tmp_path / "reference.db"
    folder = tmp_path / "samples"
    _write_sample_files(folder, "edition-a.png", "edition-b.png")
    extractor = FakeExtractor(
        {
            "edition-a.png": ExtractedReferenceSample(
                checksum_sha256="c" * 64,
                barcode=None,
                extracted_text="Artist Special Edition",
                extraction_state="fixture",
            ),
            "edition-b.png": ExtractedReferenceSample(
                checksum_sha256="d" * 64,
                barcode=None,
                extracted_text="Artist Special Edition",
                extraction_state="fixture",
            ),
        }
    )

    report = import_reference_samples(db_path, folder, extractor=extractor)

    assert report.product_count == 2
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT stable_key, barcode FROM reference_products ORDER BY stable_key"
        ).fetchall()
    assert rows == [(f"sample:{'c' * 64}", None), (f"sample:{'d' * 64}", None)]


def test_refresh_existing_replaces_local_ocr_without_reclassifying_the_product(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reference.db"
    folder = tmp_path / "samples"
    _write_sample_files(folder, "one.png")
    initial = FakeExtractor(
        {
            "one.png": ExtractedReferenceSample(
                checksum_sha256="f" * 64,
                barcode=None,
                extracted_text="garbled initial OCR",
                extraction_state="fixture:eng",
                ocr_languages=("eng",),
            )
        }
    )
    upgraded = FakeExtractor(
        {
            "one.png": ExtractedReferenceSample(
                checksum_sha256="f" * 64,
                barcode=None,
                extracted_text="中文 日本語 exact product title",
                extraction_state="fixture:eng+jpn+chi_sim",
                ocr_languages=("eng", "jpn", "chi_sim"),
            )
        }
    )

    import_reference_samples(db_path, folder, extractor=initial)
    report = import_reference_samples(
        db_path,
        folder,
        extractor=upgraded,
        refresh_existing=True,
    )

    assert report.imported_samples == 0
    assert report.existing_samples == 1
    assert report.refreshed_samples == 1
    assert report.product_count == 1
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT product_id, extracted_text, extraction_state, ocr_languages_json
            FROM reference_product_samples
            """
        ).fetchone()
    assert row == (
        1,
        "中文 日本語 exact product title",
        "fixture:eng+jpn+chi_sim",
        '["chi_sim","eng","jpn"]',
    )


def test_parse_tesseract_languages_drops_the_human_readable_header() -> None:
    assert _parse_tesseract_languages(
        "List of available languages in C:\\tessdata (3):\neng\njpn\nchi_sim\n"
    ) == ("chi_sim", "eng", "jpn")


def test_candidate_match_is_persisted_without_rejecting_an_unmatched_candidate(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reference.db"
    folder = tmp_path / "samples"
    _write_sample_files(folder, "milet.png")
    import_reference_samples(
        db_path,
        folder,
        extractor=FakeExtractor(
            {
                "milet.png": ExtractedReferenceSample(
                    checksum_sha256="e" * 64,
                    barcode="4547366558180",
                    extracted_text="milet Walkin In My Lane",
                    extraction_state="fixture",
                )
            }
        ),
    )
    pool = list_discovery_pools(db_path)[0]
    assert pool.id is not None
    candidate_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:reference-memory-token-match",
            title="milet Walkin In My Lane CD",
            availability="available",
        ),
    )
    unmatched_id = upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id,
            media_type="cd",
            identity_key="source:reference-memory-no-match",
            title="Different Artist Collector Edition",
            availability="available",
        ),
    )

    matched = refresh_discovery_candidate_reference_match(db_path, candidate_id)
    unmatched = refresh_discovery_candidate_reference_match(db_path, unmatched_id)

    assert matched.match_kind == "token_overlap"
    persisted = read_candidate_reference_match(db_path, candidate_id)
    assert persisted is not None
    assert persisted["match_kind"] == "token_overlap"
    assert persisted["score"] == matched.score
    with sqlite3.connect(db_path) as conn:
        product_id = int(conn.execute("SELECT id FROM reference_products").fetchone()[0])
    record_reference_market_observation(
        db_path,
        product_id=product_id,
        market="xianyu",
        observation_state="price_unfavorable",
        price=9_999,
        currency="CNY",
        observed_at="2026-09-11T00:00:00+00:00",
    )
    refreshed = refresh_discovery_candidate_reference_match(db_path, candidate_id)
    refreshed_persisted = read_candidate_reference_match(db_path, candidate_id)
    assert refreshed.score == matched.score
    assert refreshed_persisted is not None
    assert refreshed_persisted["score"] == matched.score
    assert unmatched.match_kind == "no_match"
    assert read_candidate_reference_match(db_path, unmatched_id) == {
        "candidate_id": unmatched_id,
        "reference_product_id": None,
        "score": 0.0,
        "match_kind": "no_match",
        "evidence": {"shared_tokens": []},
    }

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, pipeline_stage FROM discovery_candidates WHERE id = ?",
            (unmatched_id,),
        ).fetchone()
    assert row == ("active", "search_discovered")
