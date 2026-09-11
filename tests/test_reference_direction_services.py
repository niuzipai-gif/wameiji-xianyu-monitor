from __future__ import annotations

import sqlite3
from pathlib import Path

from cd_monitor.core.discovery import DiscoveryCandidate
from cd_monitor.services.reference_memory import (
    list_discovery_candidate_direction_evidence,
    list_reference_direction_summary,
)
from cd_monitor.storage.sqlite import init_db, list_discovery_pools, upsert_discovery_candidate


def _seed_references(db_path: Path) -> None:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO reference_products (id, stable_key, barcode, tokens_json, sample_count)
            VALUES (1, 'jan:490000000001', '490000000001', '[]', 2)
            """
        )
        conn.execute(
            """
            INSERT INTO reference_products (id, stable_key, barcode, tokens_json, sample_count)
            VALUES (2, 'jan:490000000002', '490000000002', '[]', 1)
            """
        )
        samples = (
            ("a" * 64, 1, "first.png", "初回限定 CD 音楽"),
            ("b" * 64, 1, "second.png", "CD album"),
            ("c" * 64, 2, "third.png", "Nintendo Switch 設定集"),
        )
        conn.executemany(
            """
            INSERT INTO reference_product_samples (
              checksum_sha256, product_id, source_path, barcode, extracted_text,
              tokens_json, extraction_state, ocr_languages_json
            ) VALUES (?, ?, ?, NULL, ?, '[]', 'fixture', '[]')
            """,
            samples,
        )


def _seed_candidate(db_path: Path, *, title: str, identity_key: str) -> int:
    pool = list_discovery_pools(db_path)[0]
    return upsert_discovery_candidate(
        db_path,
        DiscoveryCandidate(
            pool_id=pool.id or 1,
            media_type="fixture",
            identity_key=identity_key,
            title=title,
            raw_text="店铺规则 运费 角色 搜索",
        ),
    )


def test_reference_direction_summary_counts_unique_products_not_screenshots(tmp_path: Path) -> None:
    db_path = tmp_path / "reference.db"
    _seed_references(db_path)

    assert list_reference_direction_summary(db_path) == {
        "source": "approved_reference_samples",
        "reference_product_count": 2,
        "network_requests": 0,
        "directions": [
            {
                "key": "physical_music",
                "label": "实体音乐",
                "reference_product_count": 1,
                "reference_share": 0.5,
            },
            {
                "key": "console_game",
                "label": "游戏/视觉小说",
                "reference_product_count": 1,
                "reference_share": 0.5,
            },
            {
                "key": "art_or_book",
                "label": "画集/设定集/书籍",
                "reference_product_count": 1,
                "reference_share": 0.5,
            },
            {
                "key": "limited_or_first_edition",
                "label": "初回/限定/特典",
                "reference_product_count": 1,
                "reference_share": 0.5,
            },
        ],
    }


def test_candidate_direction_evidence_is_positive_only_and_has_no_decision(tmp_path: Path) -> None:
    db_path = tmp_path / "reference.db"
    _seed_references(db_path)
    candidate_id = _seed_candidate(db_path, title="限定版 CD", identity_key="candidate:music")

    assert list_discovery_candidate_direction_evidence(db_path, limit=10) == [
        {
            "candidate_id": candidate_id,
            "directions": [
                {
                    "key": "physical_music",
                    "label": "实体音乐",
                    "reference_product_count": 1,
                    "reference_share": 0.5,
                },
                {
                    "key": "limited_or_first_edition",
                    "label": "初回/限定/特典",
                    "reference_product_count": 1,
                    "reference_share": 0.5,
                },
            ],
            "state": "positive_direction_covered",
            "decision_effect": "none",
        }
    ]


def test_candidate_without_positive_direction_is_not_a_negative_label(tmp_path: Path) -> None:
    db_path = tmp_path / "reference.db"
    _seed_references(db_path)
    candidate_id = _seed_candidate(db_path, title="无关商品", identity_key="candidate:unknown")

    assert list_discovery_candidate_direction_evidence(db_path, limit=10) == [
        {
            "candidate_id": candidate_id,
            "directions": [],
            "state": "no_direction_evidence",
            "decision_effect": "none",
        }
    ]


def test_direction_services_validate_their_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "reference.db"
    _seed_references(db_path)

    for invalid_limit in (0, 201, True, "20"):
        try:
            list_discovery_candidate_direction_evidence(db_path, limit=invalid_limit)  # type: ignore[arg-type]
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid limit {invalid_limit!r}")
